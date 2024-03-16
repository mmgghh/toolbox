import multiprocessing
import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

import click
import requests

BASE_DIR = Path(__file__).parent
TMP_DIR = BASE_DIR / '.tmp'
TMP_DIR.mkdir(exist_ok=True)


def temp_file(name: str) -> Path:
    return TMP_DIR / name


def escape_special_chars(unix_path: str) -> str:
    special_chars = r'[\s(){}[\]<>|;&*?$!`\'"\\]'

    return re.sub(special_chars, r'\\\g<0>', unix_path)


def check_port(port):
    """Check if the port is available."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) != 0


def extract_user_host_port(server: str) -> tuple[str, str, str, int]:
    match = re.match(r"([\w:]+)@([\w\-.]+):(\d+)", server)
    if match:
        user_pass = match.group(1)
        if ':' in user_pass:
            user, password = user_pass.split(':')
        else:
            user, password = user_pass, None
        host = match.group(2)
        port = match.group(3)
        return user, password, host, int(port)
    else:
        raise ValueError(
            f"{server} is invalid. Make sure the server is in format <username@host:port>."
        )


def check_socks5_proxy(
        host: str, port: int,
        address='https://www.facebook.com'
):
    # Set the environment variable for the proxy
    os_https_proxy_before = os.environ.get('https_proxy')
    os.environ['https_proxy'] = f'socks5h://{host}:{port}'
    try:
        response = requests.get(address, timeout=10)
        return response.status_code == 200
    except requests.RequestException:
        return False
    finally:
        if os_https_proxy_before:
            os.environ['https_proxy'] = os_https_proxy_before
        else:
            os.environ.pop('https_proxy')


def kill_by_pid(pid: str):
    subprocess.run(['kill', '-9', pid])


def double_tunnel_base(
    user1: str, host1: str, port1: int,
    user2: str, host2: str, port2: int,
    local_port1: int = 9998,
    local_port2: int = 9999,
    public: bool = True,
    pass1_file: str = "",
    pass2_file: str = ""
):
    # Check if the ports are available
    if not check_port(local_port1) or not check_port(local_port2):
        return False

    # Determine the local host based on the public flag
    local_host = '0.0.0.0' if public else 'localhost'
    ignore_security_options = ['-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null']
    # Run the first SSH command in the background
    ssh1_cmd = ['ssh', '-L', f'{local_port1}:{host2}:{port2}', '-N', f'{user1}@{host1}', '-p', str(port1)]
    if pass1_file:
        ssh1_cmd = ['sshpass', '-f', pass1_file] + ssh1_cmd + ignore_security_options
    ssh1 = subprocess.Popen(ssh1_cmd)
    if temp_file('.ssh1_pid').exists():
        raise click.ClickException('.ssh1_pid exists before!')
    with open(temp_file('.ssh1_pid'), 'w') as ssh1file:
        ssh1file.write(str(ssh1.pid))
    # Wait for a few seconds to establish the first tunnel
    time.sleep(2)

    # Run the second SSH command in the background
    ssh2_cmd = ['ssh', '-D', f'{local_host}:{local_port2}', '-N', f'{user2}@localhost', '-p', str(local_port1)]
    if pass2_file:
        ssh2_cmd = ['sshpass', '-f', pass2_file] + ssh2_cmd + ignore_security_options
    ssh2 = subprocess.Popen(ssh2_cmd)
    if temp_file('.ssh2_pid').exists():
        raise click.ClickException('.ssh2_pid exists before!')
    with open(temp_file('.ssh2_pid'), 'w') as ssh2file:
        ssh2file.write(str(ssh2.pid))
    # Print a message to indicate the function is running
    click.echo(
        f"Double SSH tunnel is running. You can access internet through socks5://{local_host}:{local_port2}\n"
        f"Press Ctrl-C to stop."
        )


def _kill_ssh_1_and_2_processes():
    for f in (temp_file('.ssh1_pid'), temp_file('.ssh2_pid')):
        if f.exists():
            kill_by_pid(str(f.read_text()).strip())
            f.unlink()


@click.command()
@click.option('--server1',
              help="server 1 user, host and port in format 'username@host:port' or 'username:pass@host:port'")
@click.option('--server2',
              help="server 2 user, host and port in format 'username@host:port' or 'username:pass@host:port'")
@click.option('--server1-conf', type=click.Path(exists=True, dir_okay=False, readable=True),
              help="path/to/server/config containing only 1 line in format username:pass@host:port")
@click.option('--server2-conf', type=click.Path(exists=True, dir_okay=False, readable=True),
              help="path/to/server/config containing only 2 line in format username:pass@host:port")
@click.option('--lp1', required=True, default=9998, type=click.IntRange(0, 65535),
              help="local port 1")
@click.option('--lp2', required=True, default=2012, type=click.IntRange(0, 65535),
              help="local port 2")
@click.option('--public', is_flag=True, default=False, type=click.BOOL,
              help="If true, other devices on the same network can access to local port 2")
@click.option('-v', '--verbose', count=True, help='increase verbosity')
@click.option('--reconnecting', is_flag=True, default=False, type=click.BOOL,
              help="If true, the socks proxy will be checked every 5 seconds and the tunnel will be reset if necessary.")
def double_tunnel(
        server1: str, server2: str,
        server1_conf: Optional[Path], server2_conf: Optional[Path],
        lp1: int, lp2: int,
        public: bool,
        verbose: int,
        reconnecting: bool
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
    help_text = (
        '\nUsage: pyssh double-tunnel [OPTIONS]'
        '\nTry pyssh double-tunnel --help for help.'
    )
    if server1 is None and server1_conf is None:
        raise click.ClickException(f'Exactly one of server1 or server1_conf is required!{help_text}')
    if server2 is None and server2_conf is None:
        raise click.ClickException(f'Exactly one of server2 or server2_conf is required!{help_text}')
    try:
        u1, pass1, h1, p1 = extract_user_host_port(server1 or open(server1_conf, 'r').readline())
        u2, pass2, h2, p2 = extract_user_host_port(server2 or open(server2_conf, 'r').readline())
    except ValueError as e:
        raise click.ClickException(str(e) + help_text)
    else:
        pass1_temp_file = str((temp_file('.tmp_p1')).absolute())
        pass2_temp_file = str((temp_file('.tmp_p2')).absolute())
        with open(pass1_temp_file, 'w') as p1file:
            p1file.write(pass1)
        with open(pass2_temp_file, 'w') as p2file:
            p2file.write(pass2)

    def cleanup():
        Path(pass1_temp_file).unlink()
        Path(pass2_temp_file).unlink()
        _kill_ssh_1_and_2_processes()

    def monitor_and_restart_tunnel():
        """Monitor the tunnel and restart if necessary."""
        try:
            while True:
                time.sleep(5)  # Check the proxy every 5 seconds
                if check_socks5_proxy('localhost', lp2):
                    continue
                else:
                    click.echo("Facebook is not accessible through the SOCKS proxy. Restarting the tunnel...")
                    _kill_ssh_1_and_2_processes()
                    time.sleep(1)
                    process = multiprocessing.Process(
                        target=double_tunnel_base,
                        args=(u1, h1, p1, u2, h2, p2, lp1, lp2, public, pass1_temp_file, pass2_temp_file)
                    )
                    process.start()
        finally:
            cleanup()
    try:
        if reconnecting:
            monitor_and_restart_tunnel()
        else:
            double_tunnel_base(u1, h1, p1, u2, h2, p2, lp1, lp2, public, pass1_temp_file, pass2_temp_file)
    finally:
        if not reconnecting:
            cleanup()


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
        user, password, host, port = extract_user_host_port(server)
    except ValueError as e:
        click.echo(str(e), err=True)
        return

    tunnel_base(user, host, port, local_port, public)


@click.command()
@click.option('-s', '--source', required=True, prompt=True,
              help="local/path/to/target/directory or username@server:/path/to/target/directory")
@click.option('-d', '--destination', prompt=True,
              # type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
              help="local/path/to/target/directory or username@server:/path/to/target/directory")
@click.option('-p', '--ssh-port', required=True, type=click.IntRange(0, 65535),
              help="server ssh port")
@click.option('-i', '--ignore-existing', is_flag=True,
              help="do not replace existing files. (by default files that are older or "
                   "have different size will be replaced)")
@click.option('-v', '--verbose', is_flag=True, help="increase verbosity")
def rsync_dir(
        source: str, destination: str, ssh_port: int,
        ignore_existing: bool, verbose: int
):
    """
    A simple wrapper for `rsync -avzP -e "ssh -p <port>" <source> <destination>` command.
    """

    # Build the rsync command
    cmd = [
        "rsync",
        "-avzP",
        "-e", f'"ssh -p {ssh_port}"',
        "--ignore-existing" if ignore_existing else "--update",
        source,
        destination
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
