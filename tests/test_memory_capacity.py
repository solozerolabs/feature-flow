def test_twenty_gib_working_set():
    buf = bytearray(20 * 1024**3)
    for i in range(0, len(buf), 4096):
        buf[i] = 1
    assert sum(buf[::4096]) == 20 * 1024**3 // 4096
