#!/bin/sh
CONFIG=settings.ini

run_batch() {
    grep 'MODE' "${CONFIG}" | awk -F: '{print $2}'
}