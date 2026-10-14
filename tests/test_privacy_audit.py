from scripts.privacy_audit import audit, scan_bytes


def test_private_path_email_token_and_device_are_reported_without_values():
    hits=[]
    data=('C:'+ '/Users/'+'sample-person'+ '/notes '+ 'private'+'@'+'mail.invalid '+ 'ghp_'+'A'*36).encode()
    scan_bytes(data,'sample',hits,extra=['DEVICE-'+str(1234)])
    assert {hit['rule'] for hit in hits} >= {'windows-user-path','non-example-email','github-token'}
    assert all(set(hit) == {'file','rule'} for hit in hits)


def test_utf16_device_identifier_is_caught():
    hits=[]
    value='AA:BB:CC:DD:EE:FF'
    scan_bytes(value.encode('utf-16-le'),'binary',hits,extra=[value])
    assert any(hit['rule']=='private-build-identifier' for hit in hits)


def test_private_runtime_files_cannot_enter_source_release(tmp_path):
    (tmp_path/'config.json').write_text('{}')
    (tmp_path/'runtime.log').write_text('')
    result=audit(tmp_path)
    assert not result['passed']
    assert len([hit for hit in result['findings'] if hit['rule']=='private-file']) == 2


def test_upstream_contact_notices_are_allowed_but_own_identifiers_still_fail():
    hits=[]
    scan_bytes(('author'+'@'+'vendor.invalid').encode(),'LICENSE',hits,third_party=True)
    assert not hits
    scan_bytes(b'owner-private-id','LICENSE',hits,third_party=True,extra=['owner-private-id'])
    assert hits
