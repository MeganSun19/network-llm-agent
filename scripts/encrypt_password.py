#!/usr/bin/env python3
"""Password Encryption Tool: Generate encrypted passwords for device inventory"""

import sys
import os
from pathlib import Path
from cryptography.fernet import Fernet
import base64
import getpass

# Add project root directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def get_or_create_key():
    """Get or create encryption key"""
    # 1. Prioritize environment variable
    key_str = os.getenv("DEVICE_PASSWORD_KEY")
    if key_str:
        return key_str.encode()
    
    # 2. Try reading from file
    key_file = Path.home() / ".ssh" / "device_encryption.key"
    if key_file.exists():
        return key_file.read_bytes().strip()
    
    # 3. Generate new key and save
    key = Fernet.generate_key()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key)
    key_file.chmod(0o600)  # Only owner can read/write
    print(f"✅ New key generated and saved to: {key_file}")
    print(f"🔑 Key content: {key.decode()}")
    print("   Recommended to set environment variable: export DEVICE_PASSWORD_KEY='{}'".format(key.decode()))
    return key


def encrypt_password(password: str) -> str:
    """Encrypt password"""
    key = get_or_create_key()
    f = Fernet(key)
    encrypted = f.encrypt(password.encode())
    return encrypted.decode()


def decrypt_password(encrypted: str) -> str:
    """Decrypt password"""
    key = get_or_create_key()
    f = Fernet(key)
    decrypted = f.decrypt(encrypted.encode())
    return decrypted.decode()


def batch_encrypt_mode():
    """Batch encryption mode: Encrypt passwords for multiple devices at once"""
    print("=== Batch Encryption Mode ===")
    print("Encrypt passwords for multiple devices (each device can use a different password)\n")
    
    devices = {}
    
    while True:
        device_id = input("\nEnter device ID (e.g., router-1, empty line to finish): ").strip()
        if not device_id:
            break
        
        password = getpass.getpass(f"Enter password for {device_id}: ")
        if not password:
            print("⚠️  Empty password, skipping this device")
            continue
        
        encrypted = encrypt_password(password)
        devices[device_id] = {
            "password": password,
            "encrypted": encrypted
        }
        print(f"✅ {device_id} password encrypted")
    
    if not devices:
        print("\n❌ No passwords encrypted")
        return
    
    # Display results
    print("\n" + "="*70)
    print("✅ Batch encryption complete! Add the following to config/devices.yaml:")
    print("="*70)
    for device_id, data in devices.items():
        print(f"\n{device_id}:")
        print(f"  encrypted_password: \"{data['encrypted']}\"")
    print("="*70)
    
    # Verification
    verify = input("\nVerify decryption for all passwords? (y/n): ").strip().lower()
    if verify == 'y':
        print("\nVerification results:")
        all_ok = True
        for device_id, data in devices.items():
            try:
                decrypted = decrypt_password(data['encrypted'])
                if decrypted == data['password']:
                    print(f"  ✅ {device_id}: Encryption/decryption OK")
                else:
                    print(f"  ❌ {device_id}: Decryption result mismatch")
                    all_ok = False
            except Exception as e:
                print(f"  ❌ {device_id}: Decryption failed - {e}")
                all_ok = False
        
        if all_ok:
            print("\n🎉 All device passwords verified successfully!")
        else:
            print("\n⚠️  Some devices failed verification, please check")


def single_encrypt_mode():
    """Single password encryption mode"""
    print("=== Single Password Encryption Mode ===\n")
    print("Enter the device password to encrypt (input will be hidden):")
    password = getpass.getpass("Password: ")
    
    if not password:
        print("❌ Password cannot be empty")
        return
    
    encrypted = encrypt_password(password)
    
    print("\n" + "="*60)
    print("✅ Encryption complete! Add the following to config/devices.yaml:")
    print("="*60)
    print(f"encrypted_password: \"{encrypted}\"")
    print("="*60)
    
    # Verification
    verify = input("\nVerify decryption? (y/n): ").strip().lower()
    if verify == 'y':
        try:
            plain = decrypt_password(encrypted)
            if plain == password:
                print("✅ Verification successful: Encryption/decryption working properly")
            else:
                print("❌ Verification failed: Decryption result mismatch")
        except Exception as e:
            print(f"❌ Verification failed: {e}")


def decrypt_mode():
    """Decryption mode: Verify encrypted passwords"""
    print("=== Decryption Verification Mode ===\n")
    encrypted = input("Enter encrypted password: ").strip()
    try:
        plain = decrypt_password(encrypted)
        print(f"✅ Decryption successful: {plain}")
    except Exception as e:
        print(f"❌ Decryption failed: {e}")


def main():
    print("=" * 70)
    print("         Device Password Encryption Tool")
    print("=" * 70)
    print("\nSelect mode:")
    print("  1. Single password encryption (encrypt one device at a time)")
    print("  2. Batch password encryption (encrypt multiple devices at once)")
    print("  3. Decryption verification (verify encrypted passwords)")
    print("  4. Exit")
    
    choice = input("\nEnter option (1-4): ").strip()
    print()
    
    if choice == '1':
        single_encrypt_mode()
    elif choice == '2':
        batch_encrypt_mode()
    elif choice == '3':
        decrypt_mode()
    elif choice == '4':
        print("Exiting")
        return
    else:
        print("❌ Invalid option, please rerun and select 1-4")


if __name__ == "__main__":
    main()
