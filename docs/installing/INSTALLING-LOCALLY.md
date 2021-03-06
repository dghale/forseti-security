# Installing locally
* [Prerequisites](#prerequisites)
* [Create a virtualenv](#create-a-virtualenv)
* [Get the source code](#get-the-source-code)
* [Run the python setup](#run-the-python-setup)
* [Execution](#execution)

## Prerequisites
Complete the Forseti Security prerequisites [README](/docs/prerequisites/README.md).

## Create a virtualenv
```sh
$ mkvirtualenv forseti-security
$ workon forseti-security
```

## Get the source code
Clone the repo, if you haven't already done so.

```sh
$ (forseti-security): git clone https://github.com/GoogleCloudPlatform/forseti-security.git
```

## Run the python setup
Navigate to your cloned repo, then run:

```sh
$ python setup.py install
```

## Execution
You should now be able to run the following commandline tools. To see the flag options for each, use
the `--helpshort` or `--helpfull` flags.

 * `forseti_inventory` ([README](/docs/inventory/README.md))
 * `forseti_scanner` ([README](/docs/scanner/README.md))
 * `forseti_enforcer` ([README](/docs/enforcer/README.md))
