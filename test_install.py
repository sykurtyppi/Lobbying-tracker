#!/usr/bin/env python3
"""
Installation Test Script
Run this to verify your setup is correct
"""

import sys
import importlib
from pathlib import Path

def test_python_version():
    """Check Python version"""
    print("Checking Python version...")
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 9):
        print(f"❌ Python {version.major}.{version.minor} detected")
        print("   Required: Python 3.9 or higher")
        return False
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")
    return True

def test_dependencies():
    """Check if required packages are installed"""
    print("\nChecking dependencies...")
    
    required_packages = {
        'streamlit': 'Streamlit',
        'pandas': 'pandas',
        'plotly': 'Plotly',
        'yfinance': 'yfinance',
        'requests': 'requests',
        'bs4': 'BeautifulSoup4'
    }
    
    all_installed = True
    
    for package, name in required_packages.items():
        try:
            importlib.import_module(package)
            print(f"✅ {name}")
        except ImportError:
            print(f"❌ {name} - Not installed")
            all_installed = False
    
    return all_installed

def test_file_structure():
    """Check if all required files exist"""
    print("\nChecking file structure...")
    
    required_files = [
        'app.py',
        'config.py',
        'requirements.txt',
        'documentation/readme.md',
        'documentation/quickstart.md',
        'src/data_fetcher.py',
        'src/senate_scraper.py'
    ]
    
    all_present = True
    base_path = Path('.')
    
    for file in required_files:
        file_path = base_path / file
        if file_path.exists():
            print(f"✅ {file}")
        else:
            print(f"❌ {file} - Missing")
            all_present = False
    
    return all_present

def test_database():
    """Check if database can be created"""
    print("\nChecking database setup...")
    
    try:
        import sqlite3
        from pathlib import Path
        
        # Create data directory
        data_dir = Path('data')
        data_dir.mkdir(exist_ok=True)
        
        # Test database connection
        db_path = data_dir / 'test.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER)")
        conn.commit()
        conn.close()
        
        # Clean up
        db_path.unlink()
        
        print("✅ Database setup working")
        return True
        
    except Exception as e:
        print(f"❌ Database setup failed: {e}")
        return False

def main():
    """Run all tests"""
    print("=" * 60)
    print("US Lobbying Equities Strategy Tool - Installation Test")
    print("=" * 60)
    
    tests = [
        ("Python Version", test_python_version),
        ("Dependencies", test_dependencies),
        ("File Structure", test_file_structure),
        ("Database", test_database)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} failed with error: {e}")
            results.append((test_name, False))
    
    # Print summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{test_name}: {status}")
    
    all_passed = all(result for _, result in results)
    
    print("\n" + "=" * 60)
    
    if all_passed:
        print("🎉 All tests passed! You're ready to run the application.")
        print("\nTo start the application:")
        print("  ./launch.sh (Mac/Linux)")
        print("  streamlit run app.py (Windows)")
    else:
        print("⚠️ Some tests failed. Please fix the issues above.")
        print("\nTo install missing dependencies:")
        print("  pip install -r requirements.txt")
    
    print("=" * 60)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
