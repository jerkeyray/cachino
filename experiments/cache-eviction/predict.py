"""Predict vLLM prefix-cache hits under eviction for sequential requests.

This replays the free-queue policy of the pinned vLLM build (2173d712) on block
labels, so a capped-cache experiment can be predicted before it runs. It models
one worker, one KV cache group, one request in flight at a time, and prompts
whose length is not a multiple of the block size, so the single generated token
cannot change the block count. Source lines are cited in
docs/archive/e4-eviction-plan.md:

- Block 0 is a null placeholder; the other num_blocks - 1 blocks start free in
  index order.
- New blocks come from the queue head. Taking a block that still carries a
  prefix hash evicts that hash.
- A hit is the longest chain of cached full blocks from the start, capped at
  prompt length - 1 tokens. Hit blocks leave the free queue while in use.
- A finished request frees its blocks last-first. Blocks without a hash go to the
  queue head, hashed blocks to the tail, in that order.
- A reset clears every hash and keeps the queue order.

A block's identity is the exact token-ID prefix it ends, which is what chained
block hashes encode. The predictor is a hypothesis about the engine; the run
decides whether it holds.
"""
from collections import deque


class FreeQueuePredictor:
    """Track block ownership and prefix hashes across sequential requests."""

    def __init__(self, num_blocks, block_size):
        if num_blocks < 2 or block_size < 1:
            raise ValueError("need at least one usable block and a positive block size")
        self.block_size = block_size
        self.queue = deque(range(1, num_blocks))
        self.hash_of = {}  # block -> token prefix tuple
        self.refs = {}  # block -> number of in-flight requests holding it
        self.block_of = {}  # token prefix tuple -> block

    def reset(self):
        """Clear every prefix hash; free-queue order is unchanged."""
        self.hash_of.clear()
        self.block_of.clear()

    def request(self, ids, generated=0):
        """Serve one prompt to completion; return {"cached_tokens", "created_cache_tokens"}."""
        prediction, blocks = self.allocate(ids, generated)
        self.free(blocks)
        return prediction

    def allocate(self, ids, generated=0):
        """Admit one request and hold its blocks; return (prediction, blocks).

        Holding blocks until free() models overlapping requests: a block in use
        by any request is out of the free queue and cannot be evicted, and a hit
        on it only adds a reference. All of a request's blocks are taken at
        admission, while the engine allocates decode blocks as it generates, so
        overlapping traffic is approximate.

        generated > 0 models a reply of that many tokens. Its KV covers all but
        the last sampled token, which is never fed back. An integer count gives
        generated tokens unique placeholder IDs, so they can never match a later
        prompt. A list gives the actual generated IDs, which models the engine
        caching full blocks of generated tokens like prompt blocks. With
        generated == 0 the prompt length must not be a block multiple (see the
        module note).
        """
        size = self.block_size
        n = len(ids)
        if isinstance(generated, list):
            tail = list(generated[:-1])
            generated = len(generated)
        else:
            self._serial = getattr(self, "_serial", 0) + 1
            tail = [("generated", self._serial, i) for i in range(max(generated - 1, 0))]
        if n == 0 or (generated == 0 and n % size == 0) or generated < 0:
            raise ValueError("prompt length must be positive and not a multiple of the block size")
        sequence = list(ids) + tail
        full = len(sequence) // size
        keys = [tuple(sequence[:(i + 1) * size]) for i in range(full)]
        hits = []
        for key in keys[:(n - 1) // size]:
            block = self.block_of.get(key)
            if block is None:
                break
            hits.append(block)
        partial = 1 if len(sequence) % size else 0
        needed = full + partial - len(hits)
        # Check capacity before changing any state, so a caller can retry later.
        idle_hits = sum(1 for block in hits if self.refs.get(block, 0) == 0)
        if needed > len(self.queue) - idle_hits:
            raise ValueError("request does not fit in the free blocks")
        for block in hits:
            if self.refs.get(block, 0) == 0:
                self.queue.remove(block)
            self.refs[block] = self.refs.get(block, 0) + 1
        new = [self.queue.popleft() for _ in range(needed)]
        for block in new:
            self.refs[block] = 1
            old = self.hash_of.pop(block, None)
            if old is not None:
                del self.block_of[old]
        blocks = hits + new
        created = 0
        for key, block in zip(keys[len(hits):], blocks[len(hits):full]):
            if key not in self.block_of:
                self.block_of[key] = block
                self.hash_of[block] = key
                created += 1
        return {"cached_tokens": len(hits) * size, "created_cache_tokens": created * size}, blocks

    def free(self, blocks):
        """Release a request's blocks last-first, as the engine frees them."""
        for block in reversed(blocks):
            self.refs[block] -= 1
            if self.refs[block]:
                continue
            if block in self.hash_of:
                self.queue.append(block)
            else:
                self.queue.appendleft(block)


def predict(sequence, tokens, num_blocks, block_size):
    """Return {step_name: prediction} for the send steps of a spec sequence."""
    model = FreeQueuePredictor(num_blocks, block_size)
    out = {}
    for step in sequence:
        if step["action"] == "reset_prefix_cache":
            model.reset()
        elif step["action"] == "send":
            out[step["step"]] = model.request(tokens[step["probe"]])
    return out
