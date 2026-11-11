from ccc_server.oncall.engine import Engine


def test_backward_jump_and_sleep_resume_polling(tmp_path):
    eng = Engine(tmp_path)
    assert eng._due('channel', 30, 1000)
    assert not eng._due('channel', 30, 1010)
    assert eng._due('channel', 30, 100)
    assert not eng._due('channel', 30, 110)
    assert eng._due('channel', 30, 100 + 8 * 3600)
    assert not eng._due('channel', 30, 100 + 8 * 3600)
    eng.store.close()
