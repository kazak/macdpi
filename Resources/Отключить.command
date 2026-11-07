#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
/usr/bin/python3 setup.py disable
result=$?
printf '\nНажмите Enter, чтобы закрыть окно.\n'
read -r _
exit "$result"
