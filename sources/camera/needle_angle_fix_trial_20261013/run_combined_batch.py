#!/usr/bin/env python3
"""Run the isolated pressure and fuel trials on the 50-image dataset."""

import centrabus_dashboard_reader as reader
from fuel_angle_trial import read_fuel_trial

reader.READERS["fuel"] = read_fuel_trial

import test_new_50_dataset


if __name__ == "__main__":
    test_new_50_dataset.main()
