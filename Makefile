site:
	./makesite.py

deploy:
	./makesite.py deploy

clean:
	./makesite.py clean
	find . -name "__pycache__" -exec rm -r {} +
	find . -name "*.pyc" -exec rm {} +
	rm -rf .coverage htmlcov

