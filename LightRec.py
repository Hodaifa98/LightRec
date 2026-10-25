"""Main script for LightRec"""
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from lightrec.ui.app import main
    main()
