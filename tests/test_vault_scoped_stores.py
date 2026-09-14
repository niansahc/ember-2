"""
tests/test_vault_scoped_stores.py

Vault-scoped store resolution.

A vault swap changes the active vault path at runtime. The SQLite stores
that back retrieval and memory writes used to be plain module-level
singletons, so after a swap they kept serving the previous vault: reads
returned the old vault's records and writes landed in the old vault's
memory.db. These tests pin the replacement behaviour. Stores are cached
by resolved db path and accessors resolve the active vault first, so a
store belonging to another vault can never be returned.

All fixtures build synthetic vaults under tmp_path. No real vault data.
"""

from contextlib import ExitStack
from unittest.mock import patch

import pytest

from src.core.config import (
    VaultWriteBlocked,
    allow_vault_writes,
    set_vault_path_override,
)


def patch_dev_mode(label, vault_dir):
    """Put the swap endpoint in dev mode with one known vault label."""
    stack = ExitStack()
    stack.enter_context(patch("src.core.config.is_dev_mode", return_value=True))
    stack.enter_context(
        patch("src.core.config.get_known_vault_paths", return_value={label: str(vault_dir)})
    )
    stack.enter_context(patch("src.api.main.get_ember_api_key", return_value=None))
    return stack


def make_vault(root):
    """Create the minimum vault directory structure under root."""
    for subdir in ("memory/conversation", "memory/journal", "memory/state", "embeddings"):
        (root / subdir).mkdir(parents=True, exist_ok=True)
    return root


# The snapshot-and-restore fixture that used to live here now applies to the
# whole suite as `restore_process_state` in tests/conftest.py. It guarded this
# file only, while every other file that moved the override went unguarded --
# which is how the cross-file leak survived.


class TestAccessorFollowsActiveVault:
    """Accessors resolve the current vault before returning a store."""

    def test_memory_store_accessor_returns_new_store_after_vault_change(self, tmp_path):
        from src.retrieval.semantic_search import _get_memory_store

        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")
        # The read accessor only returns a store when the db file exists.
        (vault_a / "embeddings" / "memory.db").touch()
        (vault_b / "embeddings" / "memory.db").touch()

        set_vault_path_override(str(vault_a), "a")
        store_a = _get_memory_store()

        set_vault_path_override(str(vault_b), "b")
        store_b = _get_memory_store()

        assert store_a is not store_b
        assert store_a.db_path.is_relative_to(vault_a.resolve())
        assert store_b.db_path.is_relative_to(vault_b.resolve())

    def test_same_vault_returns_the_same_store(self, tmp_path):
        from src.retrieval.semantic_search import _get_memory_store

        vault = make_vault(tmp_path / "vault")
        (vault / "embeddings" / "memory.db").touch()

        set_vault_path_override(str(vault), "a")

        assert _get_memory_store() is _get_memory_store()


class TestWriteFollowsActiveVault:
    """A write after a swap lands in the new vault and nowhere else."""

    def test_write_after_swap_lands_in_new_vault_only(self, tmp_path, monkeypatch):
        from src.memory import write_memory as write_memory_module

        monkeypatch.setattr(write_memory_module, "embed_text", lambda text: [0.1, 0.2, 0.3])

        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")

        set_vault_path_override(str(vault_a), "a")
        write_memory_module.write_memory(
            text="first synthetic record",
            memory_type="conversation",
            source="test",
        )
        records_in_a = sorted((vault_a / "memory" / "conversation").glob("*.json"))
        assert len(records_in_a) == 1

        set_vault_path_override(str(vault_b), "b")
        path_b = write_memory_module.write_memory(
            text="second synthetic record",
            memory_type="conversation",
            source="test",
        )

        assert path_b.is_relative_to(vault_b.resolve())
        assert len(sorted((vault_b / "memory" / "conversation").glob("*.json"))) == 1
        # The first vault is untouched by the second write.
        assert sorted((vault_a / "memory" / "conversation").glob("*.json")) == records_in_a

        # The embedding row follows the record, not the vault that was
        # active when the store was first opened.
        from src.retrieval.store_cache import get_store

        rows_b = get_store(vault_b / "embeddings" / "memory.db").search(
            query_embedding=[0.1, 0.2, 0.3], limit=10
        )
        rows_a = get_store(vault_a / "embeddings" / "memory.db").search(
            query_embedding=[0.1, 0.2, 0.3], limit=10
        )
        assert [r["content"] for r in rows_b] == ["second synthetic record"]
        assert [r["content"] for r in rows_a] == ["first synthetic record"]


class TestReadFollowsActiveVault:
    """Retrieval after a swap searches the new vault and nothing else."""

    def test_semantic_search_after_swap_does_not_return_prior_vault_records(
        self, tmp_path, monkeypatch
    ):
        from src.memory import write_memory as write_memory_module
        from src.retrieval import semantic_search as semantic_search_module

        monkeypatch.setattr(write_memory_module, "embed_text", lambda text: [0.1, 0.2, 0.3])
        monkeypatch.setattr(semantic_search_module, "embed_text", lambda text: [0.1, 0.2, 0.3])

        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")

        set_vault_path_override(str(vault_a), "a")
        write_memory_module.write_memory(
            text="synthetic record that lives only in the first vault",
            memory_type="conversation",
            source="test",
        )
        found_in_a = semantic_search_module.semantic_search("synthetic record", limit=5)
        assert any("first vault" in r["content"] for r in found_in_a)

        set_vault_path_override(str(vault_b), "b")
        write_memory_module.write_memory(
            text="synthetic record that lives only in the second vault",
            memory_type="conversation",
            source="test",
        )
        found_in_b = semantic_search_module.semantic_search("synthetic record", limit=5)

        texts = [r["content"] for r in found_in_b]
        assert any("second vault" in t for t in texts)
        assert not any("first vault" in t for t in texts)


class TestWriteBlockRefusesWrites:
    """While the fail-closed block is set, no write funnel touches the vault."""

    def test_write_memory_raises_and_writes_nothing(self, tmp_path, monkeypatch):
        from src.core.config import allow_vault_writes, block_vault_writes
        from src.memory import write_memory as write_memory_module

        monkeypatch.setattr(write_memory_module, "embed_text", lambda text: [0.1, 0.2, 0.3])
        vault = make_vault(tmp_path / "vault")
        set_vault_path_override(str(vault), "a")

        block_vault_writes("swap verification failed")
        try:
            with pytest.raises(VaultWriteBlocked):
                write_memory_module.write_memory(
                    text="synthetic record that must not be written",
                    memory_type="conversation",
                    source="test",
                )
        finally:
            allow_vault_writes()

        assert list((vault / "memory" / "conversation").glob("*.json")) == []

    def test_state_write_raises_and_creates_no_state_dir(self, tmp_path):
        from src.core.config import allow_vault_writes, block_vault_writes
        from src.state.state_service import StateService

        vault = tmp_path / "vault"
        (vault / "memory").mkdir(parents=True, exist_ok=True)
        set_vault_path_override(str(vault), "a")

        service = StateService()
        record = service.make_record(
            state_type="open_loop",
            text="synthetic state record that must not be written",
            source="test",
        )

        block_vault_writes("swap verification failed")
        try:
            with pytest.raises(VaultWriteBlocked):
                service.write(record)
        finally:
            allow_vault_writes()

        # The block is checked before _get_state_dir(), which would
        # otherwise mkdir into an unverified vault.
        assert not (vault / "memory" / "state").exists()

    def test_writes_resume_once_the_block_is_cleared(self, tmp_path, monkeypatch):
        from src.core.config import allow_vault_writes, block_vault_writes
        from src.memory import write_memory as write_memory_module

        monkeypatch.setattr(write_memory_module, "embed_text", lambda text: [0.1, 0.2, 0.3])
        vault = make_vault(tmp_path / "vault")
        set_vault_path_override(str(vault), "a")

        block_vault_writes("swap verification failed")
        allow_vault_writes()

        path = write_memory_module.write_memory(
            text="synthetic record written after the block cleared",
            memory_type="conversation",
            source="test",
        )
        assert path is not None and path.exists()


class TestSwapVerification:
    """The swap endpoint confirms the new vault before returning 200, and
    fails closed when it cannot."""

    def _client(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        return TestClient(app)

    def _swap(self, label, vault_dir):
        return patch_dev_mode(label, vault_dir)

    def test_verified_swap_clears_a_standing_write_block(self, tmp_path):
        from src.core.config import block_vault_writes, vault_writes_blocked

        vault = make_vault(tmp_path / "demo_vault")
        block_vault_writes("left over from an earlier failed swap")

        with self._swap("demo", vault):
            resp = self._client().post(
                "/v1/developer/vault/swap", json={"vault_label": "demo"}
            )

        assert resp.status_code == 200
        assert vault_writes_blocked() is None

    def test_failed_verification_restores_prior_vault_and_blocks_writes(self, tmp_path):
        from src.core.config import get_private_vault_path, vault_writes_blocked

        prior_vault = make_vault(tmp_path / "prior_vault")
        target_vault = make_vault(tmp_path / "target_vault")
        set_vault_path_override(str(prior_vault), "prior")

        # The stores resolve somewhere outside the requested root: the
        # swap did not fully take effect and must not be reported as ok.
        elsewhere = make_vault(tmp_path / "elsewhere")
        from src.retrieval.store_cache import get_store

        rogue = get_store(elsewhere / "embeddings" / "memory.db")

        with self._swap("demo", target_vault), patch(
            "src.api.main._get_memory_store", return_value=rogue
        ):
            resp = self._client().post(
                "/v1/developer/vault/swap", json={"vault_label": "demo"}
            )

        assert resp.status_code >= 400
        assert vault_writes_blocked() is not None
        # The prior vault is restored, not left pointing at the target.
        assert get_private_vault_path() == prior_vault.resolve()

    def test_writes_are_refused_after_a_failed_swap(self, tmp_path, monkeypatch):
        from src.core.config import allow_vault_writes
        from src.memory import write_memory as write_memory_module

        monkeypatch.setattr(write_memory_module, "embed_text", lambda text: [0.1, 0.2, 0.3])
        prior_vault = make_vault(tmp_path / "prior_vault")
        target_vault = make_vault(tmp_path / "target_vault")
        elsewhere = make_vault(tmp_path / "elsewhere")
        set_vault_path_override(str(prior_vault), "prior")

        from src.retrieval.store_cache import get_store

        rogue = get_store(elsewhere / "embeddings" / "memory.db")

        with self._swap("demo", target_vault), patch(
            "src.api.main._get_memory_store", return_value=rogue
        ):
            self._client().post("/v1/developer/vault/swap", json={"vault_label": "demo"})

        try:
            with pytest.raises(VaultWriteBlocked):
                write_memory_module.write_memory(
                    text="synthetic record written after a failed swap",
                    memory_type="conversation",
                    source="test",
                )
        finally:
            allow_vault_writes()

        assert list((prior_vault / "memory" / "conversation").glob("*.json")) == []
        assert list((target_vault / "memory" / "conversation").glob("*.json")) == []
