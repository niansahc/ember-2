from src.core.config import get_private_vault_path


def test_private_vault_path_exists_as_path_object():
    path = get_private_vault_path()
    assert path is not None