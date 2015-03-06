#! /bin/bash

set -e
set -x

mkdir /tmp/build
cd /tmp/build

useradd -d /home/user -m -s /bin/bash user

yum install -y git python-devel tar gcc gcc-c++ mercurial numpy

VENV_VERSION="virtualenv-1.9.1"
rm -Rf "$VENV_VERSION"
curl -k https://pypi.python.org/packages/source/v/virtualenv/$VENV_VERSION.tar.gz | tar xfz -

VIRTUALENV=virtualenv
$VENV_VERSION/virtualenv.py --system-site-packages --no-setuptools .env

source .env/bin/activate

curl -k https://ssl.tiker.net/software/ez_setup.py | python -
curl http://git.tiker.net/pip/blob_plain/77f959a3ce9cc506efbf3a17290d387d0a6624f5:/contrib/get-pip.py | python -

pip install pyinstaller
git clone --recursive git://github.com/inducer/loopy
cd loopy

grep -v pyopencl requirements.txt > myreq.txt
pip install -r myreq.txt
python setup.py install

chown -R user /tmp/build

su user -p -c "cd /tmp/build && source .env/bin/activate && cd loopy && ./build-helpers/run-pyinstaller.sh"
