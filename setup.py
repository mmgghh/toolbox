"""Package setup for pytoolbox."""

from setuptools import find_packages, setup

setup(
    name='pytoolbox',
    version='0.1.0',
    author='MG',
    python_requires='>=3.9',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'Click',
        'openpyxl',
        'requests[socks]',
        'fpdf2',
    ],
    extras_require={
        # Needed by md2pdf to shape and reorder Persian/Arabic text correctly.
        # The DejaVu and Vazir TTFs are system-installed separately; see
        # `md2pdf --help` for instructions.
        'rtl': ['arabic-reshaper', 'python-bidi'],
    },
    entry_points={
        'console_scripts': [
            'pyfm = pytoolbox.pyfm:file_management',
            'pyssh = pytoolbox.pyssh:ssh_management',
            'pynet = pytoolbox.pynet:net_cli',
            'pyjdate = pytoolbox.pyjdate:jdate_cli',
            'pystr = pytoolbox.pystr:str_cli',
            'pytime = pytoolbox.pytime:time_cli',
            'md2pdf = pytoolbox.md2pdf:md2pdf_cli',
        ],
    },
)
