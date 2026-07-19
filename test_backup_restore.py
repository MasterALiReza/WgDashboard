import sys
import os
import shutil
import zipfile

# Set up path to access WGDashboard modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

from modules.GlobalBackup import GlobalBackupManager

def test_backup_and_restore():
    print("=== Testing Global Backup & Restore ===")
    
    # 1. Create a backup
    status, result = GlobalBackupManager.create_global_backup()
    if not status:
        print(f"[FAIL] Backup creation failed: {result}")
        return False
    
    backup_filename = result['filename']
    backup_path = os.path.join(GlobalBackupManager.get_backup_dir(), backup_filename)
    print(f"[PASS] Backup created successfully: {backup_filename}")
    
    # 2. Validate ZIP contents
    print("--- Validating ZIP contents ---")
    if not zipfile.is_zipfile(backup_path):
        print(f"[FAIL] {backup_path} is not a valid zip file")
        return False
        
    with zipfile.ZipFile(backup_path, 'r') as zf:
        namelist = zf.namelist()
        required_files = ['sql/wgdashboard_dump.sql', 'wg-dashboard.ini', 'MANIFEST.json']
        for req in required_files:
            if req not in namelist:
                print(f"[FAIL] Missing {req} in backup zip")
                return False
            print(f"[PASS] Found {req}")
    
    # 3. Test Restore (Success Case)
    print("--- Testing Restore (Success Case) ---")
    status, msg = GlobalBackupManager.restore_global_backup(backup_path)
    if not status:
        print(f"[FAIL] Restore failed: {msg}")
        return False
    print(f"[PASS] Restore successful: {msg}")
    
    # 4. Test Rollback (Failure Case)
    print("--- Testing Restore (Failure Case / Rollback) ---")
    invalid_zip = os.path.join(GlobalBackupManager.get_backup_dir(), "invalid_backup.zip")
    with open(invalid_zip, 'w') as f:
        f.write("This is not a zip file")
        
    status, msg = GlobalBackupManager.restore_global_backup(invalid_zip)
    if status:
        print(f"[FAIL] Restore succeeded on an invalid zip file? Msg: {msg}")
        return False
    
    if "Failed to extract" in msg or "not a zip file" in msg.lower():
        print(f"[PASS] Restore correctly failed and rolled back. Error: {msg}")
    else:
        print(f"[WARN] Restore failed, but unexpected message: {msg}")
        
    os.remove(invalid_zip)
    
    # 5. Cleanup
    GlobalBackupManager.delete_global_backup(backup_filename)
    print("=== All tests passed! ===")
    return True

if __name__ == "__main__":
    test_backup_and_restore()
