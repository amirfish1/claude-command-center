"""Kernel invalidation retains strict edit visibility without warm stat scans."""

import os
import struct

import pytest

from ccc_server.asset_versions import AssetVersions


@pytest.fixture
def versions():
    reader = AssetVersions()
    yield reader
    reader.close()


def test_warm_versions_share_metadata_without_filesystem_scans(tmp_path, versions, monkeypatch):
    for name in ('one.js', 'two.js'):
        (tmp_path / name).write_text('// source')
    first = versions.read(tmp_path, ['one.js', 'two.js'])
    if versions._fd is None:
        pytest.skip('kernel change notifications unavailable')
    def no_scan(*args, **kwargs):
        raise AssertionError('unchanged dependencies rescanned')
    monkeypatch.setattr(versions, '_scan', no_scan)
    assert versions.read(tmp_path, ['two.js', 'one.js']) == first
    monkeypatch.setattr(versions, '_scan', lambda root, names: ((), False) if not names else no_scan())
    assert versions.read(tmp_path, ['one.js']) == first[:1]


def test_overwrite_atomic_replace_delete_and_recreate_are_immediate(tmp_path, versions):
    asset = tmp_path / 'source.js'
    asset.write_text('first')
    previous = versions.read(tmp_path, ['source.js'])
    info = asset.stat()
    asset.write_text('other')
    os.utime(asset, ns=(info.st_atime_ns, info.st_mtime_ns))
    current = versions.read(tmp_path, ['source.js'])
    assert current != previous, 'same-size, same-mtime edits must invalidate'
    replacement = tmp_path / 'replacement'
    replacement.write_text('third')
    replacement.replace(asset)
    assert versions.read(tmp_path, ['source.js']) != current
    asset.unlink()
    assert versions.read(tmp_path, ['source.js']) == (('source.js', None, None),)
    asset.write_text('again')
    assert versions.read(tmp_path, ['source.js'])[0][3] == 5


def test_missing_nested_directory_and_new_dependencies_are_observed(tmp_path, versions):
    assert versions.read(tmp_path, ['nested/file.js']) == (('nested/file.js', None, None),)
    nested = tmp_path / 'nested'
    nested.mkdir()
    asset = nested / 'file.js'
    asset.write_text('// first')
    current = versions.read(tmp_path, ['nested/file.js'])
    assert current[0][3] == 8
    asset.write_text('// changed')
    assert versions.read(tmp_path, ['nested/file.js']) != current
    (nested / 'second.js').write_text('// another')
    assert len(versions.read(tmp_path, ['nested/file.js', 'nested/second.js'])) == 2


def test_watch_loss_and_overflow_use_strict_versions(tmp_path, versions, monkeypatch):
    asset = tmp_path / 'source.js'
    asset.write_text('first')
    previous = versions.read(tmp_path, ['source.js'])
    if versions._fd is None:
        pytest.skip('kernel change notifications unavailable')
    original = os.read
    watched = versions._fd
    def overflow(fd, count):
        return struct.pack('iIII', -1, 0x00004000, 0, 0) if fd == watched else original(fd, count)
    monkeypatch.setattr(os, 'read', overflow)
    asset.write_text('second')
    assert versions.read(tmp_path, ['source.js']) != previous
    assert versions._fd is None
    previous = versions.read(tmp_path, ['source.js'])
    asset.write_text('third')
    assert versions.read(tmp_path, ['source.js']) != previous


def test_unsupported_filesystems_keep_strict_checks(tmp_path, versions, monkeypatch):
    monkeypatch.setattr(versions, '_local_filesystem', lambda root: False)
    asset = tmp_path / 'source.js'
    asset.write_text('first')
    previous = versions.read(tmp_path, ['source.js'])
    assert versions._fd is None
    asset.write_text('second')
    assert versions.read(tmp_path, ['source.js']) != previous


def test_symlink_dependencies_follow_external_replacement(tmp_path, versions):
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / 'target.js'
    target.write_text('first')
    root = tmp_path / 'static'
    root.mkdir()
    (root / 'link.js').symlink_to(target)
    previous = versions.read(root, ['link.js'])
    assert versions._fd is None
    replacement = outside / 'replacement'
    replacement.write_text('changed')
    replacement.replace(target)
    assert versions.read(root, ['link.js']) != previous


def test_root_replacement_and_explicit_close_release_descriptor(tmp_path, versions):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    (first / 'file.js').write_text('one')
    (second / 'file.js').write_text('two')
    versions.read(first, ['file.js'])
    fd = versions._fd
    versions.read(second, ['file.js'])
    # A new descriptor may reuse the old number; the watcher follows its new root.
    (second / 'file.js').write_text('changed')
    assert versions.read(second, ['file.js'])[0][3] == 7
    versions.close()
    if fd is not None:
        with pytest.raises(OSError):
            os.fstat(versions._fd if versions._fd is not None else fd)


def test_edit_during_scan_cannot_become_a_persistent_cached_version(tmp_path, versions, monkeypatch):
    asset = tmp_path / 'source.js'
    asset.write_text('first')
    scan = versions._scan
    def concurrent_edit(root, names):
        result = scan(root, names)
        asset.write_text('a different source')
        return result
    monkeypatch.setattr(versions, '_scan', concurrent_edit)
    before = versions.read(tmp_path, ['source.js'])
    monkeypatch.setattr(versions, '_scan', scan)
    assert versions.read(tmp_path, ['source.js']) != before
