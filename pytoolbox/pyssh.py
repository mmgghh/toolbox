import re
import signal
import subprocess
import time
from pathlib import Path

import click


def double_tunnel_base(
    user1: str, host1: str, port1: int,
    user2: str, host2: str, port2: int,
    local_port1: int = 9998,
    local_port2: int = 9999,
    public: bool = True
):
    # Run the first SSH command in the background and save its process ID
    ssh1 = subprocess.Popen(
        ['ssh', '-L', f'{local_port1}:{host2}:{port2}', '-N', f"{user1}@{host1}", '-p', str(port1)])
    # Wait for a few seconds to establish the first tunnel
    time.sleep(2)
    # Run the second SSH command in the background and save its process ID
    local_host = '0.0.0.0' if public else 'localhost'
    ssh2 = subprocess.Popen(['ssh', '-D', f'{local_host}:{local_port2}', '-N', f"{user2}@localhost", '-p', str(local_port1)])

    # Define a handler function for terminating signals
    def handler(signum, frame):
        # Kill the background processes
        ssh1.kill()
        ssh2.kill()
        # Print a message to indicate the function is stopped
        click.echo("Double SSH tunnel is stopped.")

    # Register the handler function for SIGINT and SIGTERM signals
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    # Print a message to indicate the function is running
    click.echo(
        "Double SSH tunnel is running.\n"
        f"You can access internet through socks5://localhost:{local_port2}\n"
        "Press Ctrl-C to stop."
    )
    # Wait for the background processes to finish
    ssh1.wait()


def tunnel_base(
    user: str, host: str, port: int,
    local_port: int = 10099,
    public: bool = True
):
    local_host = '0.0.0.0' if public else 'localhost'
    ssh = subprocess.Popen(
        ['ssh', '-D', f'{local_host}:{local_port}', '-N', f"{user}@{host}", '-p', str(port)])

    # Define a handler function for terminating signals
    def handler(signum, frame):
        ssh.kill()
        click.echo("Double SSH tunnel is stopped.")

    # Register the handler function for SIGINT and SIGTERM signals
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    # Print a message to indicate the function is running
    click.echo(
        "SSH tunnel is running.\n"
        f"You can access internet through socks5://localhost:{local_port}\n"
        "Press Ctrl-C to stop."
    )
    # Wait for the background processes to finish
    ssh.wait()


@click.command()
@click.option('-s', '--server', required=True, prompt=True,
              help="server username, host and port in format 'username@host:port'")
@click.option('-p', '--local-port', required=True, default=9998, type=click.IntRange(0, 65535),
              help="local port")
@click.option('--public', is_flag=True, default=False, type=click.BOOL,
              help="If true, <local port> will be accessible publicly.")
@click.option('-v', '--verbose', count=True, help='increase verbosity')
def tunnel(server: str, local_port: int, public: bool, verbose: int):
    """
    This function creates a socks proxy that routes the traffic from your local machine to a remote server.
    This is handy when:
        1. Your local machine have limited internet access.
        2. The server has unrestricted internet access.
        3. Your local machine can reach the server.

    After calling this function you can use socks5://localhost:<local-port> or
    socks5://<your-ip>:<local-port> if public is true and enjoy free internet.
    """
    try:
        user, host, port = extract_user_host_port(server)
    except ValueError as e:
        click.echo(str(e), err=True)
        return

    tunnel_base(user, host, port, local_port, public)


def extract_user_host_port(server: str):
    match = re.match(r"(\w+)@([\w.]+):(\d+)", server)
    if match:
        user = match.group(1)
        host = match.group(2)
        port = match.group(3)
        return user, host, port
    else:
        raise ValueError(
            f"{server} is invalid. Make sure the server is in format <username@host:port>."
        )


@click.command()
@click.option('--server1', required=True, prompt=True,
              help="server 1 user, host and port in format 'username@host:port'")
@click.option('--server2', required=True, prompt=True,
              help="server 2 user, host and port in format 'username@host:port'")
@click.option('--lp1', required=True, default=9998, type=click.IntRange(0, 65535),
              help="local port 1")
@click.option('--lp2', required=True, default=9999, type=click.IntRange(0, 65535),
              help="local port 2")
@click.option('--public', is_flag=True, default=False, type=click.BOOL,
              help="If true, other devices on the same network can access to local port 2")
@click.option('-v', '--verbose', count=True, help='increase verbosity')
def double_tunnel(
        server1: str, server2: str,
        lp1: int, lp2: int,
        public: bool,
        verbose: int
):
    """
    This function creates a socks proxy that routes the traffic from your local machine to server 2 via server 1.
    This is handy when:
        1. Your local machine and server 1 have limited internet access.
        2. Server 2 has unrestricted internet access.
        3. Your local machine can reach server 1 (but not 2).
        4. Server 1 can reach server 2.

    After calling this function you can use socks5://localhost:<local-port-2> or
    socks5://<your-ip>:<local-port-2> if public is true and enjoy free internet.
    """
    try:
        u1, h1, p1 = extract_user_host_port(server1)
        u2, h2, p2 = extract_user_host_port(server2)
    except ValueError as e:
        click.echo(str(e), err=True)
        return

    double_tunnel_base(u1, h1, p1, u2, h2, p2, lp1, lp2, public)


@click.command()
@click.option('--server', required=True, prompt=True,
              help="server username, host and port in format 'username@host:port'")
@click.option('-s', '--source', required=True, prompt=True, type=click.Path(path_type=Path), 
              help="path to source directory on remote machine")
@click.option('-d', '--destination', prompt=True,
              type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path), 
              help="path to destination directory on local machine")
@click.option('-v', '--verbose', is_flag=True, help="increase verbosity")
def rsync_dir(
        server: str, source: Path, destination: Path, verbose: int):
    """
    A simple wrapper for `rsync -avzP -e "ssh -p <port>" --ignore-existing <user>@<remote_address>:<source> <destination>` command.
    """
    try:
        user, host, port = extract_user_host_port(server)
    except ValueError as e:
        click.echo(str(e), err=True)
        return

    # Build the rsync command
    cmd = [
        "rsync",
        "-avzP",
        "-e", f'"ssh -p {port}"',
        "--ignore-existing",
        f"{user}@{host}:{source}",
        f"{destination}"
    ]

    # Add the verbose flag if True
    if verbose:
        cmd.append("-v")

    command = ' '.join(cmd)
    # Run the command and click.echo() the output
    click.echo(f"Running: {' '.join(cmd)}")
    subprocess.run(command, shell=True, capture_output=False)


@click.group()
def ssh_management():
    pass


ssh_management.add_command(double_tunnel)
ssh_management.add_command(tunnel)
ssh_management.add_command(rsync_dir)

if __name__ == '__main__':
    ssh_management()
