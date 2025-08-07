from setuptools import setup, find_packages
import os

# Read the README file for long description
def read_readme():
    readme_path = os.path.join(os.path.dirname(__file__), 'README.md')
    if os.path.exists(readme_path):
        with open(readme_path, 'r', encoding='utf-8') as f:
            return f.read()
    return "UDP communication with LightBurn software"

setup(
    name="lightburn-udp",
    version="1.0.0",
    author="Urs Helfenstein",
    author_email="uhelfenstein@users.noreply.github.com",
    description="UDP communication with LightBurn software",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/uhelfenstein/lightburn-udp",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Hardware",
    ],
    python_requires=">=3.7",
    install_requires=[
        # Add any dependencies here
    ],
    extras_require={
        "dev": [
            "pytest>=6.0",
            "pytest-cov",
            "black",
            "flake8",
        ],
    },
    keywords="lightburn udp laser engraving cnc",
    project_urls={
        "Bug Reports": "https://github.com/uhelfenstein/lightburn-udp/issues",
        "Source": "https://github.com/uhelfenstein/lightburn-udp",
        "Documentation": "https://github.com/uhelfenstein/lightburn-udp#readme",
    },
)