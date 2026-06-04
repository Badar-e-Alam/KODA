import pytest
from users import list_users

def test_default_pagination():
    result = list_users()
    assert result["users"] == _USERS[:10]
    assert result["total"] == 25

def test_second_page():
    result = list_users(page=2, per_page=10)
    assert len(result["users"]) == 10
    assert result["page"] == 2

def test_last_partial_page():
    result = list_users(page=3, per_page=10)
    assert len(result["users"]) == 5

def test_out_of_range():
    result = list_users(page=10, per_page=10)
    assert result["users"] == []
    assert result["total"] == 25

def test_invalid_page():
    with pytest.raises(ValueError):
        list_users(page=0)

def test_invalid_per_page():
    with pytest.raises(ValueError):
        list_users(per_page=-1)
