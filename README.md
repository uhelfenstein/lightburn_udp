# LightBurn UDP Communication

A Python library for communicating with LightBurn software via UDP protocol.

## Features

- Send UDP commands to LightBurn
- Check LightBurn status
- Load files programmatically
- Start jobs
- Automatic LightBurn startup
- Error handling and timeouts

## Installation

```bash
pip install lightburn-udp
```

## Quick Start

```python
from lightburn_udp.lightburn_udp import LightBurnUDPCommunication

# Initialize with your LightBurn executable path
lb = LightBurnUDPCommunication(r"C:\Program Files\LightBurn\LightBurn.exe")

# Ensure LightBurn is running
if lb.ensure_lightburn_running():
    # Check status
    status = lb.get_status()
    print(f"LightBurn status: {status}")

    # Load a file
    lb.load_file(r"C:\path\to\your\file.lbrn2")

    # Start the job
    lb.start_job()
```

## API Reference

### LightBurnUDPCommunication

#### Methods

- `ping(timeout=1.0)`: Check if LightBurn is responding
- `get_status(timeout=1.0)`: Get current status ("Idle" or "Running")
- `load_file(file_path, force=False, timeout=1.0)`: Load a file
- `start_job(timeout=1.0)`: Start the current job
- `close_lightburn(force=False, timeout=1.0)`: Close LightBurn
- `ensure_lightburn_running(startup_timeout=10.0)`: Start LightBurn if not running

## Requirements

- Python 3.7+
- LightBurn software

## License

MIT License