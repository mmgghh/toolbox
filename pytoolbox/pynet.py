import re
import subprocess
from pathlib import Path

import click
import requests


@click.command()
@click.option('-n', '--name', required=True, prompt=True, type=str)
@click.option('-p', '--proxy', type=str)
def imdb_info(name: str, proxy: str):
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
    link_pattern = rf'<a class=\"ipc-metadata-list-summary-item__t\".*?href=\"([^\"]+)\".*?{original_name}'
    if not (link_search := re.search(link_pattern, search_result)):
        click.echo(f'No result found for {original_name}. (page-not-found)')
        return

    link = link_search.group(1)
    target_url = f"https://www.imdb.com{link}"
    target_page = requests.get(target_url, proxies=proxies, headers=headers).text.replace('\n', '')
    if rate_result := re.compile(r'IMDb RATING.*?>(\d\.\d)<').search(target_page):
        name_pattern = r'<h1.*hero__pageTitle.*?<span[^>]*>([^<]+)?</span>'
        if name_search := re.search(name_pattern, target_page):
            exact_name = name_search.group(1)
        else:
            # click.echo('Could not extract the exact name\n', err=True)
            exact_name = original_name

        genres = ','.join(
            re.findall(
                r'<span class="ipc-chip__text">(.*?)</span></a>',
                target_page
            )
        )
        if description_search := re.search(
            r'<p data-testid="plot".*?data-testid="plot-l".*?>(.*?)</span>',
            target_page
        ):
            description = description_search.group(1)
        else:
            description = ''
        click.echo(f"{exact_name} --> rate: {rate_result.group(1)} | genre: {genres} | description: {description}")
    else:
        click.echo(f'No result found for {original_name}')


@click.command()
@click.option('-s', '--source', required=True, prompt=True,
              type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
              help='The source file should contain the name of movies/series in separate lines.')
@click.option('-p', '--proxy', type=str)
def sort_by_imdb_rate(source: Path, proxy: str | None):
    targets = []
    for name in source.read_text().strip().split('\n'):
        result = subprocess.run(
            f'pynet imdb-info -n "{name}"' + (f' -p {proxy}' if proxy else ''),
            shell=True,
            stdout=subprocess.PIPE
        ).stdout.decode().strip()

        if result:
            if 'No result found' in result:
                rate = -1
            else:
                rate = float(re.search(r'rate: (\d+(\.\d*)?)', result).group(1))

            targets.append((name if rate == -1 else result, rate))
            click.echo(result)

    destination = source.parent / f'sorted-{source.name}'
    with open(destination, 'w') as output:
        for t in sorted(targets, key=lambda x: x[1], reverse=True):
            output.write(f'{t[0]}\n')
    click.echo(f'Check out {destination.absolute()}')


@click.group()
def net_cli():
    pass


for cmd in (
        imdb_info,
        sort_by_imdb_rate
):
    net_cli.add_command(cmd)


if __name__ == '__main__':
    net_cli()
