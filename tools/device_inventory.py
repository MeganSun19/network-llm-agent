"""Device Inventory Management Module: Supports YAML config files + password encryption + concurrent execution"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor

try:
    import yaml
except ImportError:
    yaml = None

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

# Use paramiko for synchronous execution
import paramiko
import re


class DeviceInventory:
    """Device inventory manager"""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize device inventory
        
        Args:
            config_path: YAML config file path, defaults to config/devices.yaml
        """
        if config_path is None:
            project_root = Path(__file__).parent.parent
            config_path = project_root / "config" / "devices.yaml"
        
        self.config_path = Path(config_path)
        self.devices: Dict[str, Dict] = {}
        self.groups: Dict[str, List[str]] = {}
        self._encryption_key: Optional[bytes] = None
        
        if self.config_path.exists():
            self._load_config()
    
    def _load_config(self):
        """Load configuration from YAML file"""
        if yaml is None:
            raise ImportError("Need to install pyyaml: pip install pyyaml")
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        self.devices = config.get('devices', {})
        self.groups = config.get('device_groups', {})
    
    def _get_encryption_key(self) -> Optional[bytes]:
        """Get encryption key"""
        if self._encryption_key:
            return self._encryption_key
        
        # 1. Environment variable
        key_str = os.getenv("DEVICE_PASSWORD_KEY")
        if key_str:
            self._encryption_key = key_str.encode()
            return self._encryption_key
        
        # 2. File
        key_file = Path.home() / ".ssh" / "device_encryption.key"
        if key_file.exists():
            self._encryption_key = key_file.read_bytes().strip()
            return self._encryption_key
        
        return None
    
    def _decrypt_password(self, encrypted: str) -> str:
        """Decrypt password"""
        if Fernet is None:
            raise ImportError("Need to install cryptography: pip install cryptography")
        
        key = self._get_encryption_key()
        if not key:
            raise ValueError("Encryption key not found, please set DEVICE_PASSWORD_KEY or create a key file")
        
        f = Fernet(key)
        return f.decrypt(encrypted.encode()).decode()
    
    def get_device(self, device_id: str) -> Optional[Dict]:
        """Get device configuration (automatically decrypt password)"""
        if device_id not in self.devices:
            return None
        
        device = self.devices[device_id].copy()
        
        # Handle password: prioritize encrypted password
        if 'encrypted_password' in device:
            try:
                device['password'] = self._decrypt_password(device['encrypted_password'])
            except Exception as e:
                device['_password_error'] = str(e)
        
        return device
    
    def get_group_devices(self, group_name: str) -> List[str]:
        """Get all device IDs in a device group"""
        return self.groups.get(group_name, [])
    
    def list_devices(self) -> List[str]:
        """List all device IDs"""
        return list(self.devices.keys())
    
    def list_groups(self) -> List[str]:
        """List all device groups"""
        return list(self.groups.keys())


def _execute_ssh_sync(host: str, username: str, password: str, command: str, 
                      port: int = 22, timeout: int = 10) -> Dict[str, Any]:
    """Execute SSH command synchronously (using paramiko, with fallback mechanism)"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(host, port=port, username=username, password=password, 
                      timeout=timeout, look_for_keys=False, allow_agent=False)
        
        output = ""
        
        # Method 1: Try using invoke_shell (suitable for IOS devices)
        try:
            import time
            shell = client.invoke_shell()
            time.sleep(1)  # Wait for shell to be ready
            
            # Clear welcome message
            if shell.recv_ready():
                shell.recv(65535)
            
            # Disable terminal pagination to get full output
            shell.send("terminal length 0\n")
            time.sleep(0.5)
            if shell.recv_ready():
                shell.recv(65535)  # Clear the response
            
            # Send command
            shell.send(command + "\n")
            time.sleep(2)  # Wait for command execution
            
            # Receive output
            while shell.recv_ready():
                output += shell.recv(65535).decode('utf-8', errors='ignore')
                time.sleep(0.1)
            
            # If no output, wait a bit more
            if not output:
                time.sleep(1)
                if shell.recv_ready():
                    output = shell.recv(65535).decode('utf-8', errors='ignore')
            
            shell.close()
            
        except Exception:
            # Method 2: If invoke_shell fails, try exec_command
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            output = stdout.read().decode('utf-8', errors='ignore')
            error = stderr.read().decode('utf-8', errors='ignore')
            if error:
                output = output + "\n" + error
        
        # Clean output: remove ANSI escape codes
        output = re.sub(r'\x1b\[[0-9;]*[mGKHf]', '', output)
        lines = output.strip().split('\n')
        # Filter out command echo line
        if lines and command in lines[0]:
            lines = lines[1:]
        
        return {
            "ok": True,
            "host": host,
            "output": '\n'.join(lines).strip()
        }
    
    except paramiko.AuthenticationException:
        return {"ok": False, "host": host, "error": f"Authentication failed ({username}@{host})"}
    except paramiko.SSHException as e:
        return {"ok": False, "host": host, "error": f"SSH error: {str(e)}"}
    except Exception as e:
        return {"ok": False, "host": host, "error": f"Execution error: {str(e)}"}
    finally:
        client.close()


def execute_on_devices(device_ids: List[str], command: str, 
                       inventory: Optional[DeviceInventory] = None,
                       max_workers: int = 5) -> Dict[str, Dict]:
    """
    Execute commands concurrently on multiple devices
    
    Args:
        device_ids: Device ID list or device group name
        command: Command to execute
        inventory: Device inventory instance (None creates automatically)
        max_workers: Maximum concurrency
    
    Returns:
        {device_id: {ok, host, output/error}}
    """
    if inventory is None:
        inventory = DeviceInventory()
    
    # Check if it is a device group
    if len(device_ids) == 1 and device_ids[0] in inventory.list_groups():
        device_ids = inventory.get_group_devices(device_ids[0])
    
    results = {}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for dev_id in device_ids:
            device = inventory.get_device(dev_id)
            if not device:
                results[dev_id] = {"ok": False, "error": f"Device {dev_id} does not exist"}
                continue
            
            if '_password_error' in device:
                results[dev_id] = {"ok": False, "error": f"Password decryption failed: {device['_password_error']}"}
                continue
            
            future = executor.submit(
                _execute_ssh_sync,
                host=device['host'],
                username=device['username'],
                password=device['password'],
                command=command,
                port=device.get('port', 22),
                timeout=device.get('timeout', 10)
            )
            futures[future] = dev_id
        
        for future in futures:
            dev_id = futures[future]
            try:
                results[dev_id] = future.result()
            except Exception as e:
                results[dev_id] = {"ok": False, "error": str(e)}
    
    return results
