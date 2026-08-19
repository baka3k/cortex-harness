#!/bin/sh
CONFIG=settings.ini

run_batch() {
    . other_target.sh
    grep 'MODE' "${CONFIG}" | awk -F: '{print $2}'
}

source missing.sh
