import sys
import os
import subprocess

print("-" * 30)
print("PYTHON ENVIRONMENT DIAGNOSTIC")
print("-" * 30)
print(f"Executable: {sys.executable}")
print(f"Version: {sys.version}")
print(f"Platform: {sys.platform}")
print(f"CWD: {os.getcwd()}")
print("\nSYS PATH:")
for p in sys.path:
    print(f"  - {p}")

print("\nCHECKING STRIPE:")
try:
    import stripe
    print(f"  [SUCCESS] stripe object: {stripe}")
    try:
        print(f"  Location: {stripe.__file__}")
    except AttributeError:
        print("  Location: [NO __file__ ATTRIBUTE - likely a namespace package or built-in conflict]")
    try:
        print(f"  Version: {stripe.__version__}")
    except AttributeError:
        print("  Version: [NO __version__ ATTRIBUTE]")
except ImportError:
    print("  [ERROR] stripe is NOT installed in this environment.")
except Exception as e:
    print(f"  [ERROR] Unexpected error importing stripe: {e}")

print("\nCHECKING UVICORN PATH:")
try:
    # Use 'where' on Windows or 'which' on Linux/Mac
    cmd = "where.exe" if os.name == "nt" else "which"
    result = subprocess.run([cmd, "uvicorn"], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  uvicorn is here: {result.stdout.strip()}")
    else:
        print("  uvicorn NOT found in PATH")
except Exception as e:
    print(f"  Could not check uvicorn path: {e}")
print("-" * 30)
