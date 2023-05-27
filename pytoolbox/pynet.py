import re

import click
import requests


@click.command()
@click.option('-n', '--name', required=True, prompt=True, type=str)
@click.option('-p', '--proxy', type=str)
def imdb_rate(name: str, proxy: str):
    """Gets a name and prints the IMDb rate."""
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    # imdb blocks requests default agent
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"
    }
    original_name = name
    name = name.replace(" ", "+")
    url = f"https://www.imdb.com/find?q={name}&s=tt"
    search_result = requests.get(url, proxies=proxies, headers=headers).text
    if not_found := re.compile(r'>(No results found for[^<]+)<').search(search_result):
        click.echo(not_found.group(1), err=True)
        return
    pattern = re.compile(rf'<a class=\"ipc-metadata-list-summary-item__t\".*?href=\"([^\"]+)\".*?{original_name}')
    link = pattern.search(search_result).group(1)
    target_url = f"https://www.imdb.com{link}"
    response = requests.get(target_url, proxies=proxies, headers=headers).text
    if rate_result := re.compile(r'IMDb RATING.*?>(\d\.\d)<').search(response):
        name_pattern = re.compile(r'<h1.*hero__pageTitle.*?<span[^>]*>([^<]+)?</span>')
        click.echo(f"The IMDb rating of {name_pattern.search(response).group(1)} is {rate_result.group(1)}.")
    else:
        click.echo(f'No result found for {original_name}')


@click.group()
def net_cli():
    pass


for cmd in (
    imdb_rate,
):
    net_cli.add_command(cmd)


if __name__ == '__main__':
    net_cli()
