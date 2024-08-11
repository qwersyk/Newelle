application_id = 'io.github.qwersyk.Newelle'

scalable_dir = join_paths('hicolor', 'scalable', 'apps')
install_data(
  join_paths(scalable_dir, ('@0@.svg').format(application_id)),
  install_dir: join_paths(get_option('datadir'), 'icons', scalable_dir)
)

symbolic_dir = join_paths('hicolor', 'symbolic', 'apps')
install_data(
  join_paths(symbolic_dir, ('@0@-symbolic.svg').format(application_id)),
  install_dir: join_paths(get_option('datadir'), 'icons', symbolic_dir)
)

symbolic_dir = join_paths(get_option('datadir'), 'icons/hicolor/symbolic/apps')
install_data (
    'internet-symbolic.svg',
    install_dir: symbolic_dir
)
install_data (
    'warning-outline-symbolic.svg',
    install_dir: symbolic_dir
)
install_data (
    'star-filled-rounded-symbolic.svg',
    install_dir: symbolic_dir
)
install_data (
    'update-symbolic.svg',
    install_dir: symbolic_dir
)
install_data (
    'search-folder-symbolic.svg',
    install_dir: symbolic_dir
)
install_data (
    'avatar-symbolic.svg',
    install_dir: symbolic_dir
)