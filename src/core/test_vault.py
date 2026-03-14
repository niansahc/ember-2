from config import get_private_vault_path


vault = get_private_vault_path()

print("Private vault path:", vault)
print("Vault exists:", vault.exists())
