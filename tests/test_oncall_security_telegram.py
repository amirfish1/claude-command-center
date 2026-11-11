import pytest
from ccc_server.oncall import replies
from ccc_server.oncall.store import Inbound

@pytest.mark.parametrize("kind", ["group", "supergroup", "channel"])
def test_group_member_is_not_owner(kind):
    entry = {"type": "telegram", "owner": "-12345"}
    msg = Inbound("tg", "1", "-12345", "allow", raw={"chat_type": kind})
    assert not replies.is_owner(entry, msg)

def test_private_owner_is_accepted():
    assert replies.is_owner({"type": "telegram", "owner": "12345"},
        Inbound("tg", "1", "12345", "allow", raw={"chat_type": "private"}))
