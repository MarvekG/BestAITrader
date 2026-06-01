#!/usr/bin/env python3
"""
Hard Password Reset Script

Usage: Update user password directly in the database when forgotten.
Instructions:
1. Ensure you are in the backend project root (backend/)
2. Activate your virtual environment (e.g., conda activate ATrader)
3. Run: python scripts/reset_password.py (will read settings from .env)
"""

import sys
from pathlib import Path

# Add backend root to PYTHONPATH to support importing app module
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.crud.user import get_user_by_username, update_user
from app.schemas.user import UserUpdate

def reset_password(username: str, new_password: str):
    print(f"[*] Preparing to reset password for user '{username}'...")
    db = SessionLocal()
    try:
        user = get_user_by_username(db, username=username)
        if not user:
            print(f"[!] Error: User '{username}' not found.")
            return False
            
        print(f"[*] Found User ID: {user.id}")
        
        # Build UserUpdate schema, utilize existing internal hash generation logic in update_user
        user_update = UserUpdate(
            username=user.username,
            email=user.email,
            password=new_password
        )
        updated_user = update_user(db, user.id, user_update)
        
        if updated_user:
            print(f"[+] Success: Password for '{username}' has been hard reset.")
            return True
        else:
            print("[!] Error: Database update failed.")
            return False
            
    except Exception as e:
        print(f"[!] Exception occurred: {str(e)}")
        return False
    finally:
        db.close()

if __name__ == "__main__":
    admin_username = settings.FIRST_SUPERUSER
    admin_password = settings.FIRST_SUPERUSER_PASSWORD
    
    if not admin_password:
        print("[!] Error: FIRST_SUPERUSER_PASSWORD config is empty.")
        sys.exit(1)
        
    print(f"[*] Loaded admin config: {admin_username}")
    
    if len(admin_password) < 6:
        print("[!] Error: Configured password length must be at least 6 characters.")
        sys.exit(1)
        
    success = reset_password(admin_username, admin_password)
    sys.exit(0 if success else 1)
