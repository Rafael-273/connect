import os
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient

TENANT_ID = os.getenv("AZURE_TENANT_ID")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
KEY_VAULT_NAME = os.getenv("AZURE_KEY_VAULT_NAME", "kvfiladelfia")
KV_URI = f"https://{KEY_VAULT_NAME}.vault.azure.net/"

credential = ClientSecretCredential(
    tenant_id=TENANT_ID,
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET
)

client = SecretClient(vault_url=KV_URI, credential=credential)

def get_speech_key():
    secret_name = "speechkey"
    secret = client.get_secret(secret_name)
    return secret.value

speech_key = get_speech_key()
