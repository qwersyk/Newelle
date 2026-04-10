#include <Python.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PYTHON_VERSION "3.13"

static int parent_dir(const char *input, char *output, size_t size) {
    char buffer[PATH_MAX];
    snprintf(buffer, sizeof(buffer), "%s", input);
    char *dir = dirname(buffer);
    if (dir == NULL) {
        return -1;
    }
    snprintf(output, size, "%s", dir);
    return 0;
}

static int append_wide_path(PyWideStringList *list, const char *path) {
    wchar_t *wide_path = Py_DecodeLocale(path, NULL);
    if (wide_path == NULL) {
        return -1;
    }
    PyStatus status = PyWideStringList_Append(list, wide_path);
    PyMem_RawFree(wide_path);
    return PyStatus_Exception(status) ? -1 : 0;
}

int main(int argc, char **argv) {
    char executable_path[PATH_MAX];
    uint32_t size = sizeof(executable_path);
    if (_NSGetExecutablePath(executable_path, &size) != 0) {
        fprintf(stderr, "Failed to get executable path\n");
        return 1;
    }

    char resolved_path[PATH_MAX];
    if (realpath(executable_path, resolved_path) == NULL) {
        perror("realpath");
        return 1;
    }

    char macos_dir[PATH_MAX];
    char contents_dir[PATH_MAX];
    char frameworks_dir[PATH_MAX];
    char resources_dir[PATH_MAX];
    char app_root[PATH_MAX];
    char share_dir[PATH_MAX];
    char lib_dir[PATH_MAX];
    char bin_dir[PATH_MAX];
    char python_home[PATH_MAX];
    char python_bin[PATH_MAX];
    char pkg_data_dir[PATH_MAX];
    char locale_dir[PATH_MAX];
    char schema_dir[PATH_MAX];
    char gi_typelib_dir[PATH_MAX];
    char gdk_pixbuf_module_dir[PATH_MAX];
    char icon_dir[PATH_MAX];
    char src_dir[PATH_MAX];
    char macos_support_dir[PATH_MAX];
    char python_stdlib_dir[PATH_MAX];
    char python_dynload_dir[PATH_MAX];
    char python_site_dir[PATH_MAX];
    char path_env[PATH_MAX * 2];

    if (parent_dir(resolved_path, macos_dir, sizeof(macos_dir)) != 0 ||
        parent_dir(macos_dir, contents_dir, sizeof(contents_dir)) != 0) {
        fprintf(stderr, "Failed to resolve application directories\n");
        return 1;
    }

    snprintf(frameworks_dir, sizeof(frameworks_dir), "%s/Frameworks", contents_dir);
    snprintf(resources_dir, sizeof(resources_dir), "%s/Resources", contents_dir);
    snprintf(app_root, sizeof(app_root), "%s/app", resources_dir);
    snprintf(share_dir, sizeof(share_dir), "%s/share", resources_dir);
    snprintf(lib_dir, sizeof(lib_dir), "%s", frameworks_dir);
    snprintf(bin_dir, sizeof(bin_dir), "%s/bin", resources_dir);
    snprintf(python_home, sizeof(python_home), "%s/Python.framework/Versions/%s", frameworks_dir, PYTHON_VERSION);
    snprintf(python_bin, sizeof(python_bin), "%s/bin/python%s", python_home, PYTHON_VERSION);
    snprintf(pkg_data_dir, sizeof(pkg_data_dir), "%s/newelle", share_dir);
    snprintf(locale_dir, sizeof(locale_dir), "%s/locale", share_dir);
    snprintf(schema_dir, sizeof(schema_dir), "%s/glib-2.0/schemas", share_dir);
    snprintf(gi_typelib_dir, sizeof(gi_typelib_dir), "%s/lib/girepository-1.0", resources_dir);
    snprintf(gdk_pixbuf_module_dir, sizeof(gdk_pixbuf_module_dir), "%s/lib/gdk-pixbuf-2.0/2.10.0/loaders", resources_dir);
    snprintf(icon_dir, sizeof(icon_dir), "%s/icons", share_dir);
    snprintf(src_dir, sizeof(src_dir), "%s/src", app_root);
    snprintf(macos_support_dir, sizeof(macos_support_dir), "%s/macos", app_root);
    snprintf(python_stdlib_dir, sizeof(python_stdlib_dir), "%s/lib/python%s", python_home, PYTHON_VERSION);
    snprintf(python_dynload_dir, sizeof(python_dynload_dir), "%s/lib/python%s/lib-dynload", python_home, PYTHON_VERSION);
    snprintf(python_site_dir, sizeof(python_site_dir), "%s/lib/python%s/site-packages", python_home, PYTHON_VERSION);

    setenv("GI_TYPELIB_PATH", gi_typelib_dir, 1);
    setenv("DYLD_FALLBACK_LIBRARY_PATH", lib_dir, 1);
    setenv("GSETTINGS_SCHEMA_DIR", schema_dir, 1);
    setenv("GSETTINGS_BACKEND", "keyfile", 1);
    setenv("XDG_DATA_DIRS", share_dir, 1);
    setenv("NEWELLE_ICON_DIR", icon_dir, 1);
    setenv("NEWELLE_APP_NAME", "Newelle", 1);
    setenv("NEWELLE_ROOT", app_root, 1);
    setenv("NEWELLE_PKG_DATA_DIR", pkg_data_dir, 1);
    setenv("NEWELLE_LOCALE_DIR", locale_dir, 1);
    setenv("NEWELLE_LIB_DIR", lib_dir, 1);
    setenv("NEWELLE_BIN_DIR", bin_dir, 1);
    setenv("NEWELLE_PYTHON_BIN", python_bin, 1);
    setenv("GDK_PIXBUF_MODULEDIR", gdk_pixbuf_module_dir, 1);
    setenv("GDK_PIXBUF_MODULE_FILE", "", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    setenv("PYTHONHOME", python_home, 1);
    setenv("PYTHONEXECUTABLE", python_bin, 1);
    const char *existing_path = getenv("PATH");
    snprintf(path_env, sizeof(path_env), "%s:%s", bin_dir, existing_path != NULL ? existing_path : "/usr/bin:/bin:/usr/sbin:/sbin");
    setenv("PATH", path_env, 1);

    const char *runtime_ready = getenv("NEWELLE_RUNTIME_READY");
    if (runtime_ready == NULL || strcmp(runtime_ready, "1") != 0) {
        setenv("NEWELLE_RUNTIME_READY", "1", 1);
        execv(resolved_path, argv);
        perror("execv");
        return 1;
    }

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.parse_argv = 0;
    config.use_environment = 0;
    config.user_site_directory = 0;
    config.write_bytecode = 0;

    status = PyConfig_SetBytesString(&config, &config.program_name, python_bin);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        Py_ExitStatusException(status);
    }

    status = PyConfig_SetBytesString(&config, &config.home, python_home);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        Py_ExitStatusException(status);
    }

    status = PyWideStringList_Append(&config.argv, L"Newelle");
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        Py_ExitStatusException(status);
    }
    for (int i = 1; i < argc; ++i) {
        if (append_wide_path(&config.argv, argv[i]) != 0) {
            PyConfig_Clear(&config);
            fprintf(stderr, "Failed to configure Python arguments\n");
            return 1;
        }
    }

    config.module_search_paths_set = 1;
    if (append_wide_path(&config.module_search_paths, python_stdlib_dir) != 0 ||
        append_wide_path(&config.module_search_paths, python_dynload_dir) != 0 ||
        append_wide_path(&config.module_search_paths, python_site_dir) != 0 ||
        append_wide_path(&config.module_search_paths, app_root) != 0 ||
        append_wide_path(&config.module_search_paths, macos_support_dir) != 0 ||
        append_wide_path(&config.module_search_paths, src_dir) != 0) {
        PyConfig_Clear(&config);
        fprintf(stderr, "Failed to configure Python module paths\n");
        return 1;
    }

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }

    PyObject *module = PyImport_ImportModule("run_newelle");
    if (module == NULL) {
        PyErr_Print();
        Py_Finalize();
        return 1;
    }

    PyObject *main_func = PyObject_GetAttrString(module, "main");
    Py_DECREF(module);
    if (main_func == NULL) {
        PyErr_Print();
        Py_Finalize();
        return 1;
    }

    PyObject *result = PyObject_CallNoArgs(main_func);
    Py_DECREF(main_func);
    if (result == NULL) {
        PyErr_Print();
        Py_Finalize();
        return 1;
    }

    int exit_code = 0;
    if (PyLong_Check(result)) {
        exit_code = (int)PyLong_AsLong(result);
    }
    Py_DECREF(result);
    Py_Finalize();
    return exit_code;
}
