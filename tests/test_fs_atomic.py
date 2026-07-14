from agency.fs.atomic import atomic_write_bytes


def test_atomic_write_replaces_complete_bytes(tmp_path):
    path = tmp_path / "state.bin"
    path.write_bytes(b"old")
    atomic_write_bytes(path, b"new\x00payload")
    assert path.read_bytes() == b"new\x00payload"