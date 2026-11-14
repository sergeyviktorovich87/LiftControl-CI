"""Read the root manifest of a trusted administrator's APK without extracting it.

Only metadata is read here. Android verifies the signing certificate on the device.
Binary XML layout: AOSP androidfw/ResourceTypes.h (ResStringPool/ResXMLTree).
"""
import struct
import zipfile

ANDROID = 'http://schemas.android.com/apk/res/android'
PACKAGE = 'ru.liftcontrol.app.beta'


def manifest_metadata(data):
    def u16(at):
        return struct.unpack_from('<H', data, at)[0]

    def u32(at):
        return struct.unpack_from('<I', data, at)[0]

    if len(data) < 8 or u16(0) != 3 or u16(2) != 8 or u32(4) != len(data):
        raise ValueError('APK_XML_HEADER')
    strings = []
    offset = 8
    while offset + 8 <= len(data):
        kind, header, size = struct.unpack_from('<HHI', data, offset)
        end = offset + size
        if header < 8 or size < header or end > len(data):
            raise ValueError('APK_XML_CHUNK')
        if kind == 1:
            if header < 28:
                raise ValueError('APK_XML_POOL')
            count, styles, flags, start, style_start = struct.unpack_from('<IIIII', data, offset + 8)
            if count > 65536 or header + 4 * (count + styles) > start or start >= size:
                raise ValueError('APK_XML_STRINGS')
            pool_end = offset + (style_start or size)

            def length(at, utf8):
                first = data[at] if utf8 else u16(at)
                step, mask = (1, 0x80) if utf8 else (2, 0x8000)
                at += step
                if first & mask:
                    second = data[at] if utf8 else u16(at)
                    return ((first & (mask - 1)) << (8 if utf8 else 16)) | second, at + step
                return first, at

            for index in range(count):
                at = offset + start + u32(offset + header + index * 4)
                if not offset + start <= at < pool_end:
                    raise ValueError('APK_XML_STRING_OFFSET')
                if flags & 0x100:
                    _, at = length(at, True)
                    count_bytes, at = length(at, True)
                    terminator = 1
                    encoding = 'utf-8'
                else:
                    count_chars, at = length(at, False)
                    count_bytes = count_chars * 2
                    terminator = 2
                    encoding = 'utf-16-le'
                if at + count_bytes + terminator > pool_end or any(data[at + count_bytes:at + count_bytes + terminator]):
                    raise ValueError('APK_XML_STRING_LENGTH')
                strings.append(data[at:at + count_bytes].decode(encoding))
        elif kind == 0x102:
            if header < 16 or header + 20 > size or not strings:
                raise ValueError('APK_XML_ELEMENT')
            ext = offset + header
            if strings[u32(ext + 4)] != 'manifest':
                raise ValueError('APK_XML_ROOT')
            attr_start, attr_size, count = struct.unpack_from('<HHH', data, ext + 8)
            if attr_start < 20 or attr_size < 20 or header + attr_start + count * attr_size > size:
                raise ValueError('APK_XML_ATTRIBUTES')
            values = {}
            for index in range(count):
                at = ext + attr_start + index * attr_size
                namespace, name, raw = struct.unpack_from('<III', data, at)
                namespace = None if namespace == 0xffffffff else strings[namespace]
                name = strings[name]
                if not (namespace is None and name == 'package' or namespace == ANDROID and name in ('versionCode', 'versionName', 'versionCodeMajor')):
                    continue
                value_type, value = data[at + 15], u32(at + 16)
                if value_type == 3:
                    value = strings[value]
                elif value_type not in (0x10, 0x11):
                    raise ValueError('APK_XML_VALUE')
                if name in values:
                    raise ValueError('APK_XML_DUPLICATE')
                values[name] = value
            code, version = values.get('versionCode'), values.get('versionName')
            if values.get('package') != PACKAGE or values.get('versionCodeMajor', 0) != 0:
                raise ValueError('APK_PACKAGE')
            if type(code) is not int or not 0 < code <= 2100000000:
                raise ValueError('APK_VERSION_CODE')
            if not isinstance(version, str) or not version.strip() or len(version) > 40 or any(ord(c) < 32 for c in version):
                raise ValueError('APK_VERSION_NAME')
            return {'version': version, 'version_code': code}
        offset = end
    raise ValueError('APK_MANIFEST_MISSING')


def read_apk(file):
    with zipfile.ZipFile(file) as archive:
        items = archive.infolist()
        manifests = [i for i in items if i.filename == 'AndroidManifest.xml']
        if len(manifests) != 1 or not 1 <= manifests[0].file_size <= 2 * 1024**2 or len(items) > 20000:
            raise ValueError('APK_MANIFEST_LIMIT')
        if not any(i.filename.startswith('lib/arm64-v8a/') for i in items):
            raise ValueError('APK_ABI')
        return manifest_metadata(archive.read(manifests[0]))
