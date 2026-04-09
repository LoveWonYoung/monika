.PHONY: dev test build

dev:
	cd myo3 && python -m pip install --upgrade pip setuptools wheel maturin && python -m maturin develop

test:
	cd myo3 && python -m unittest discover -s tests -p 'test_*.py' -v

build:
	cd myo3 && python -m maturin build
