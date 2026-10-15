def test_twenty_gib_working_set():
    # Twenty 1 GiB chunks, every page touched so the memory is really committed;
    # a single 20 GiB allocation is refused up front with MemoryError.
    chunks = []
    for _ in range(20):
        chunk = bytearray(1024**3)
        for i in range(0, len(chunk), 4096):
            chunk[i] = 1
        chunks.append(chunk)

    assert sum(sum(c[::4096]) for c in chunks) == 20 * 1024**3 // 4096
