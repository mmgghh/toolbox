# Import the sshtunnel module
import signal
import subprocess
import time

import click


# Define a function called double_ssh_tunnel with six parameters
def double_ssh_tunnel_base(
        user1: str, server1: str, port1: int,
        user2: str, server2: str, port2: int
):
    # Run the first SSH command in the background and save its process ID
    ssh1 = subprocess.Popen(
        ['ssh', '-L', f'9998:{server2}:{port2}', '-N', f"{user1}@{server1}", '-p', str(port1)])
    # Wait for a few seconds to establish the first tunnel
    time.sleep(2)
    # Run the second SSH command in the background and save its process ID
    ssh2 = subprocess.Popen(['ssh', '-D', '9999', '-N', f"{user2}@localhost", '-p', '9998'])

    # Define a handler function for terminating signals
    def handler(signum, frame):
        # Kill the background processes
        ssh1.kill()
        ssh2.kill()
        # Print a message to indicate the function is stopped
        print("Double SSH tunnel is stopped.")

    # Register the handler function for SIGINT and SIGTERM signals
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    # Print a message to indicate the function is running
    print(
        "Double SSH tunnel is running.\n"
        f"You can access internet through socks5://localhost:9999\n"
        "Press Ctrl-C to stop."
    )
    # Wait for the background processes to finish
    ssh1.wait()


@click.command()
@click.option('--u1', required=True, prompt=True)
@click.option('--s1', required=True, prompt=True)
@click.option('--p1', required=True, prompt=True, type=click.IntRange(0, 65535))
@click.option('--u2', required=True, prompt=True)
@click.option('--s2', required=True, prompt=True)
@click.option('--p2', required=True, prompt=True, type=click.IntRange(0, 65535))
@click.option('-v', '--verbose', count=True)
def double_ssh_tunnel(u1: str, s1: str, p1: int, u2: str, s2: str, p2: int, verbose: int):
    double_ssh_tunnel_base(u1, s1, p1, u2, s2, p2)


@click.group()
def ssh_management():
    pass


ssh_management.add_command(double_ssh_tunnel)

if __name__ == '__main__':
    ssh_management()
