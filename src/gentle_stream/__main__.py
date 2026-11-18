"""`python -m gentle_stream`: how the TUI starts the background player."""
import sys

from . import main

if sys.argv[1:2] == ['--desktop-child']:
    from .desktop_session import child_main

    sys.exit(child_main(sys.argv[2:]))
sys.exit(main())
