import signal
import subprocess
import time
from pathlib import Path

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
@click.option('--u1', required=True, prompt=True, help="server 1 user")
@click.option('--s1', required=True, prompt=True, help="server 1")
@click.option('--p1', required=True, prompt=True, type=click.IntRange(0, 65535),
              help="server 1 port")
@click.option('--u2', required=True, prompt=True, help="server 2 user")
@click.option('--s2', required=True, prompt=True, help="server 2")
@click.option('--p2', required=True, prompt=True, type=click.IntRange(0, 65535),
              help="server 2 port")
@click.option('-v', '--verbose', count=True)
def double_ssh_tunnel(u1: str, s1: str, p1: int, u2: str, s2: str, p2: int, verbose: int):
    """
    Creates an ssh tunnel to server 1 and from there to server 2.
    Works for situations when you want to use server 1 as a bridge to server 2 that has free
    access to internet.
    By running this function you can access internet through socks5://localhost:9999.
    """
    double_ssh_tunnel_base(u1, s1, p1, u2, s2, p2)


@click.command()
@click.option('-u', '--user', required=True, prompt=True, help="remote user")
@click.option('-r', '--remote-address', required=True, prompt=True, help="server address")
@click.option('-p', '--port', required=True, prompt=True, type=click.IntRange(0, 65535), 
              help="remote port")
@click.option('-s', '--source', required=True, prompt=True, type=click.Path(path_type=Path), 
              help="path to source directory on remote machine")
@click.option('-d', '--destination', prompt=True,
              type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path), 
              help="path to destination directory on local machine")
@click.option('-v', '--verbose', is_flag=True, help="increase verbosity")
def rsync_dir(
        user: str, remote_address: str, port: int, source: Path, destination: Path, verbose: int):
    """
    A simple wrapper for `rsync -avzP -e "ssh -p <port>" --ignore-existing <user>@<remote_address>:<source> <destination>` command.
    """
    # Build the rsync command
    cmd = [
        "rsync",
        "-avzP",
        "-e", f'"ssh -p {port}"',
        "--ignore-existing",
        f"{user}@{remote_address}:{source}",
        f"{destination}"
    ]

    # Add the verbose flag if True
    if verbose:
        cmd.append("-v")

    command = ' '.join(cmd)
    # Run the command and print the output
    click.echo(f"Running: {' '.join(cmd)}")
    subprocess.run(command, shell=True, capture_output=False)


@click.group()
def ssh_management():
    pass


ssh_management.add_command(double_ssh_tunnel)
ssh_management.add_command(rsync_dir)

if __name__ == '__main__':
    ssh_management()
