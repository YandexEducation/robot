# boot.py -- run on boot-up
# can run arbitrary Python, but best to keep it minimal

import machine
import micropython
micropython.alloc_emergency_exception_buf(100)
