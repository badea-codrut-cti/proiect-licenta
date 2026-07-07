"""
Exam Processor - A CLI tool for processing geometry exam images.
"""

import sys

# Python 3.11+ caps integer string conversion at 4300 digits by default.
# LLM outputs can occasionally contain very large numeric literals
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)

__version__ = "0.1.0"
