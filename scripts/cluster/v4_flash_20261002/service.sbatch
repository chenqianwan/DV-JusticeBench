#!/bin/bash
#SBATCH --job-name=chenlong_dvj_v4flash
#SBATCH --partition=llm
#SBATCH --qos=llm
#SBATCH --account=hansirui
#SBATCH --nice=0
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --gres=gpu:8
#SBATCH --mem=512G
#SBATCH --time=5-00:00:00
#SBATCH --no-requeue
#SBATCH --chdir=/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002
#SBATCH --output=/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002/logs/slurm-%j.out
#SBATCH --error=/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002/logs/slurm-%j.err
#SBATCH --export=NONE

set -euo pipefail
umask 077
export PATH=/usr/bin:/bin
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1
export SLURM_EXPORT_ENV=ALL
export SLURM_CONF=/cm/shared/apps/slurm/var/etc/slurm/slurm.conf
unset PYTHONPATH
exec /usr/bin/python3 -B /aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002/allocation_supervisor.py
