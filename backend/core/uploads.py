from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from rest_framework.parsers import MultiPartParser
from rest_framework.exceptions import ParseError


class BoundedUploadHandler(FileUploadHandler):
    """Stop oversized streams before Django spools them to private temporary disk."""
    def handle_raw_input(self,input_data,META,content_length,boundary,encoding=None):
        self.total = 0
        self.files = 0
        self.max_files = 5 if self.request.path.rstrip('/').endswith('/chat') else 1
        self.limit = (20 if '/documents/' in self.request.path or '/media/uploads/' in self.request.path or self.max_files == 5 else 10)*1024*1024
        self.too_large = bool(content_length and content_length > self.limit+1024*1024)
    def new_file(self,*args,**kwargs):
        super().new_file(*args,**kwargs)
        self.files += 1
        if self.too_large or self.files > self.max_files:
            self.request.upload_error = True
            raise StopUpload(connection_reset=True)
    def receive_data_chunk(self,raw_data,start):
        self.total += len(raw_data)
        if self.total > self.limit:
            self.request.upload_error = True
            raise StopUpload(connection_reset=True)
        return raw_data
    def file_complete(self,file_size):
        return None


class BoundedMultiPartParser(MultiPartParser):
    def parse(self,stream,media_type=None,parser_context=None):
        result = super().parse(stream,media_type,parser_context)
        request = parser_context['request']
        if getattr(request,'upload_error',False):
            raise ParseError('Превышен размер или количество вложений. В чате — до 5 файлов и 20 МБ, в фотоотчёте — одно фото до 10 МБ.')
        return result
