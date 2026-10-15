"""Memory-capacity regression test.

Proves the validation sandbox really provides a 20 GiB working set. The size
is fixed on purpose: on a host with less memory the kernel OOM-kills the
process (exit 137), which is the expected signal, not a defect in this test.
"""


def test_twenty_gib_working_set():
    buf = bytearray(20 * 1024**3)
    # Touch one byte per page so every page is actually committed.
    for i in range(0, len(buf), 4096):
        buf[i] = 1
    assert sum(buf[::4096]) == 20 * 1024**3 // 4096
