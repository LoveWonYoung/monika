.PHONY: dev test build

dev:
	python -m pip install --upgrade pip setuptools wheel maturin && python -m maturin develop

test:
	python -m unittest discover -s tests -t . -p 'test_*.py' -v

build:
	python -m maturin build
