# toolbox 

toolbox is a package of useful scripts for managing files and many more, but the many more part is not implemented yet. In theory it should work on linux, windows, mac and termux, but only tested on ubuntu and python 3.9.6.

## features

- file management
  - partitioning: Split a directory into multiple direcories. (based on size or number of files)

## instructions
1. clone the project.
2. create and activate a virtual environment.
3. install toolbox.
4. run.

### example usage on ubuntu
```shell
$ git clone https://github.com/mmgghh/toolbox.git
$ python3 -m venv venv && source venv/bin/activate
$ pip install --editable .
$ pyfm --help
$ pyfm partition --help
```
