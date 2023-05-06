# toolbox 

toolbox is a package of useful Python scripts for managing files. In theory it should work on linux, windows, mac and termux, but only tested on ubuntu and python >= 3.9.6.

## Features

- **File management**
  - pattition: Creates subdirectories within the destination directory and distributes the contents of source directory based on the number or size of them.
  - merge: Merges (moves) the contents of a source directory into a destination directory.
  - batch_find_replace: Finds and replaces all the matching texts with replacement string in all files with target extensions in target directory.
  - batch_rename: Finds and replaces all matching texts in files/directories name with replacement string in target directory and its subdirectories.
  - generate_text_file: Generates some text files with each line containing a random sentence.
- **SSH**
    - double_ssh_tunnel: Creates an ssh tunnel to server 1 and from there to server 2. Helps in situations when you want to use server 1 as a bridge to server 2 that has free access to internet. By running this function you can access internet through socks5://localhost:9999.


## Instructions
1. clone the project.
2. create and activate a virtual environment.
3. install toolbox.
4. run.

### Example usage on ubuntu
```shell
$ git clone https://github.com/mmgghh/toolbox.git
$ python3 -m venv venv && source venv/bin/activate
$ pip install --editable .
$ pyfm --help
$ pyssh --help
$ pyfm partition --help
$ pyssh double-ssh-tunnel --help
$
$ # split /some/dir into 5 partitions (dirs)
$ pyfm partition --partitions 5 --source /some/dir --split-based-on size
$
$ # split /source directory to multiple directories with an approximate size of 15 megabyte
$ pyfm partition --split-size 15 -s /source --dir-prefix dir -v
$
$ # merge all files in /source and its sub-directories into /destination
$ pyfm merge -s /source -d /destination --overwrite keep-both
$
```
