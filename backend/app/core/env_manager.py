import os
import re
from typing import Optional

class EnvManager:
    """
    Utility class to manage .env file operations.
    Handles reading and updating key-value pairs in the .env file.
    """
    
    def __init__(self, env_path: str = None):
        if env_path:
            self.env_path = env_path
        else:
            # Default to backend root .env
            # Assuming this file is at backend/app/core/env_manager.py
            # backend root is ../../.env
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.env_path = os.path.join(os.path.dirname(os.path.dirname(current_dir)), ".env")

    def set_key(self, key: str, value: str) -> bool:
        """
        Set or update a key-value pair in the .env file.
        If the key exists, it updates the value.
        If the key doesn't exist, it appends it to the file.
        """
        try:
            # Create .env if it doesn't exist
            if not os.path.exists(self.env_path):
                with open(self.env_path, 'w') as f:
                    f.write("")

            with open(self.env_path, 'r') as f:
                lines = f.readlines()

            key_found = False
            new_lines = []
            
            # Simple regex to match KEY=VALUE, handling optional quotes
            # This is a basic implementation; robust parsing might require python-dotenv (but avoiding extra deps if possible)
            # We assume standard KEY=VALUE format
            
            for line in lines:
                stripped_line = line.strip()
                if stripped_line.startswith('#') or not stripped_line:
                    new_lines.append(line)
                    continue
                
                # Check for key match before the first '='
                if stripped_line.split('=')[0].strip() == key:
                    new_lines.append(f"{key}={value}\n")
                    key_found = True
                else:
                    new_lines.append(line)
            
            if not key_found:
                # Ensure existing file ends with newline before appending
                if new_lines and not new_lines[-1].endswith('\n'):
                    new_lines[-1] = new_lines[-1] + '\n'
                new_lines.append(f"{key}={value}\n")

            with open(self.env_path, 'w') as f:
                f.writelines(new_lines)
            
            return True
        except Exception as e:
            print(f"Error updating .env file: {e}")
            return False

    def get_key(self, key: str) -> Optional[str]:
        """
        Read a value directly from .env file (bypassing loaded environment variables).
        """
        if not os.path.exists(self.env_path):
            return None
            
        try:
            with open(self.env_path, 'r') as f:
                for line in f:
                    stripped_line = line.strip()
                    if stripped_line.startswith('#') or not stripped_line:
                        continue
                    
                    parts = stripped_line.split('=', 1)
                    if len(parts) == 2 and parts[0].strip() == key:
                        return parts[1].strip().strip('"').strip("'")
            return None
        except Exception:
            return None

# Global instance
env_manager = EnvManager()
