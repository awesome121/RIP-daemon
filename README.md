# RIP_router_daemon
Routering daemon implementing Simplified RIPv2, it also implements split horizon with poisoned reverse.
We use port number instead of IP address.

# Run
$./daemon.py router1
$./daemon.py router2
$./daemon.py router3
...

# Note
This demo intends to run on a single computer but use different port numbers to simulate different routers.
