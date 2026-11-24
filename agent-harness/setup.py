from setuptools import find_namespace_packages, setup

setup(
    name='cli-anything-omni-trading',
    version='0.1.0',
    description='Omni AI Trading command line (read the records, plan and apply settings; never orders)',
    packages=find_namespace_packages(include=['cli_anything.*']),
    package_data={'cli_anything.omni_trading': ['skills/*.md', 'README.md']},
    python_requires='>=3.11',
    install_requires=['click>=8.1'],
    entry_points={'console_scripts': ['omni = cli_anything.omni_trading.omni_trading_cli:main',
                                      'cli-anything-omni-trading = cli_anything.omni_trading.omni_trading_cli:main']},
)
