from setuptools import setup, find_packages

setup(
    name='pytoolbox',
    version='0.1.0',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'Click',
        'requests',
    ],
    entry_points={
        'console_scripts': [
            'pyfm = pytoolbox.pyfm:file_management',
            'pyssh = pytoolbox.pyssh:ssh_management',
            'pynet = pytoolbox.pynet:net_cli',
        ],
    },
)
