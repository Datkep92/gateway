#import "gw.h"

@interface BackupVC () <UIDocumentPickerDelegate>
@property (nonatomic, strong) NSDictionary *pending;
@end

@implementation BackupVC

- (void)viewDidLoad {
    self.title = @"Sao lưu & khôi phục";
    [super viewDidLoad];
}

- (void)loadData {
    [self.sections removeAllObjects];

    GWSection *out = [self addSection:@"Sao lưu"
        footer:@"Tệp chứa toàn bộ nhà cung cấp, key thượng nguồn, model, key cho máy khác và cài đặt. Giữ tệp cẩn thận."];
    GWRow *e = [self addRow:out title:@"Xuất cấu hình ra tệp"
                        sub:@"Tạo tệp JSON rồi mở hộp chia sẻ để lưu"];
    e.tap = ^(GWRow *row) { [self exportBackup]; };

    GWSection *in = [self addSection:@"Khôi phục"
        footer:@"Khôi phục sẽ thay toàn bộ cấu hình hiện có bằng nội dung tệp (trong một lượt, không làm nửa vời)."];
    GWRow *p = [self addRow:in title:@"Chọn tệp sao lưu…"
                        sub:@"Chọn tệp .json đã xuất từ Cổng"];
    p.tap = ^(GWRow *row) { [self pickFile]; };

    GWRow *c = [self addRow:in title:@"Dùng nội dung đã chép trong Clipboard"
                        sub:@"Khi không chọn được tệp: chép nội dung JSON rồi bấm đây"];
    c.tap = ^(GWRow *row) { [self importFromClipboard]; };

    [self.tableView reloadData];
}

#pragma mark - xuất

- (void)exportBackup {
    [self busy:@"Đang tạo tệp…"];
    [[GWA shared] send:@"GET" path:@"/gateway/config/export" body:nil done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không xuất được"]; return; }
        if (![j isKindOfClass:[NSDictionary class]]) { [self fail:@"Máy chủ trả về dữ liệu lạ."]; return; }
        [self writeAndShare:j];
    }];
}

- (void)writeAndShare:(NSDictionary *)cfg {
    NSError *err = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:cfg
                                                   options:(NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys)
                                                     error:&err];
    if (data == nil) { [self fail:[NSString stringWithFormat:@"Không tạo được tệp: %@", err.localizedDescription]]; return; }

    NSDateFormatter *f = [[NSDateFormatter alloc] init];
    f.dateFormat = @"yyyyMMdd-HHmm";
    NSString *name = [NSString stringWithFormat:@"gateway-backup-%@.json", [f stringFromDate:[NSDate date]]];
    NSString *tmp = [NSTemporaryDirectory() stringByAppendingPathComponent:name];
    if (![data writeToFile:tmp atomically:YES]) { [self fail:@"Không ghi được tệp tạm."]; return; }

    UIActivityViewController *av = [[UIActivityViewController alloc]
        initWithActivityItems:@[[NSURL fileURLWithPath:tmp]] applicationActivities:nil];
    av.popoverPresentationController.sourceView = self.view;
    av.popoverPresentationController.sourceRect = CGRectMake(self.view.bounds.size.width / 2,
                                                             self.view.bounds.size.height / 2, 1, 1);
    [self presentViewController:av animated:YES completion:^{
        [self toast:@"Chọn “Lưu vào Tệp” để giữ tệp"];
    }];
}

#pragma mark - nhập

- (void)pickFile {
    UIDocumentPickerViewController *p = [[UIDocumentPickerViewController alloc]
        initWithDocumentTypes:@[@"public.json", @"public.text", @"public.data"]
                       inMode:UIDocumentPickerModeImport];
    p.delegate = self;
    p.allowsMultipleSelection = NO;
    [self presentViewController:p animated:YES completion:nil];
}

- (void)documentPicker:(UIDocumentPickerViewController *)c didPickDocumentsAtURLs:(NSArray<NSURL *> *)urls {
    NSURL *u = urls.firstObject;
    if (u == nil) return;
    BOOL scoped = [u startAccessingSecurityScopedResource];
    NSData *d = [NSData dataWithContentsOfURL:u];
    if (scoped) [u stopAccessingSecurityScopedResource];
    if (d == nil) { [self fail:@"Không đọc được tệp đã chọn."]; return; }
    [self useData:d source:u.lastPathComponent ?: @"tệp"];
}

- (void)importFromClipboard {
    NSString *s = [UIPasteboard generalPasteboard].string;
    if (s.length == 0) { [self fail:@"Clipboard đang trống."]; return; }
    [self useData:[s dataUsingEncoding:NSUTF8StringEncoding] source:@"Clipboard"];
}

- (void)useData:(NSData *)d source:(NSString *)source {
    if (d.length > 4 * 1024 * 1024) { [self fail:@"Tệp lớn hơn 4 MB, cổng không nhận."]; return; }
    NSError *err = nil;
    id j = [NSJSONSerialization JSONObjectWithData:d options:0 error:&err];
    if (![j isKindOfClass:[NSDictionary class]]) {
        [self fail:[NSString stringWithFormat:@"Không đọc được JSON: %@", err.localizedDescription ?: @"nội dung lạ"]];
        return;
    }
    NSString *format = j[@"format"];
    if ([format isKindOfClass:[NSString class]] && ![format isEqualToString:@"gateway-backup"]) {
        [self fail:[NSString stringWithFormat:@"Tệp không phải bản sao lưu của Cổng (%@).", format]];
        return;
    }
    NSString *extra = [format isKindOfClass:[NSString class]] ? @"" : @" Tệp không ghi định dạng, vẫn thử khôi phục?";
    NSInteger provs = [j[@"providers"] isKindOfClass:[NSArray class]] ? [j[@"providers"] count] : 0;
    NSInteger keys = [j[@"client_keys"] isKindOfClass:[NSArray class]] ? [j[@"client_keys"] count] : 0;
    NSInteger models = [j[@"models"] isKindOfClass:[NSArray class]] ? [j[@"models"] count] : 0;
    NSString *msg = [NSString stringWithFormat:
        @"Nguồn: %@\n%ld nhà cung cấp · %ld model · %ld key cho máy khác\n\nToàn bộ cấu hình hiện có sẽ bị thay.%@",
        source, (long)provs, (long)models, (long)keys, extra];
    UIAlertController *ac = [UIAlertController alertControllerWithTitle:@"Khôi phục cấu hình?"
                                                              message:msg
                                                       preferredStyle:UIAlertControllerStyleAlert];
    [ac addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    [ac addAction:[UIAlertAction actionWithTitle:@"Khôi phục" style:UIAlertActionStyleDestructive
                                        handler:^(UIAlertAction *a) { [self submit:(NSDictionary *)j]; }]];
    [self presentViewController:ac animated:YES completion:nil];
}

- (void)submit:(NSDictionary *)cfg {
    [self busy:@"Đang khôi phục…"];
    [[GWA shared] send:@"POST" path:@"/gateway/config/import" body:cfg done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không khôi phục được"]; return; }
        NSString *newKey = j[@"client_key"];
        if ([newKey isKindOfClass:[NSString class]] && newKey.length > 0) gwSetAppKey(newKey);

        NSString *counts = [self countsText:j[@"counts"]];
        BOOL restart = [j[@"restart_needed"] boolValue];
        NSString *note = restart
            ? [NSString stringWithFormat:@"\n\nCổng trong tệp là %@, khác cổng đang nghe — cần khởi động lại cổng.",
               j[@"port"] ?: @"?"]
            : @"";
        UIAlertController *ac = [UIAlertController
            alertControllerWithTitle:@"Đã khôi phục"
                             message:[NSString stringWithFormat:@"%@%@", counts, note]
                      preferredStyle:UIAlertControllerStyleAlert];
        if (restart) {
            [ac addAction:[UIAlertAction actionWithTitle:@"Khởi động lại ngay"
                                                  style:UIAlertActionStyleDestructive
                                                handler:^(UIAlertAction *a) { [self restartNow]; }]];
        }
        [ac addAction:[UIAlertAction actionWithTitle:@"Đóng" style:UIAlertActionStyleDefault handler:nil]];
        [self presentViewController:ac animated:YES completion:^{ [self loadData]; }];
    }];
}

- (NSString *)countsText:(id)counts {
    if (![counts isKindOfClass:[NSDictionary class]]) return @"Cấu hình đã được thay.";
    NSMutableArray *parts = [NSMutableArray array];
    for (NSString *k in [counts allKeys]) [parts addObject:[NSString stringWithFormat:@"%@: %@", k, counts[k]]];
    return [NSString stringWithFormat:@"Đã thay cấu hình.\n%@", [parts componentsJoinedByString:@"\n"]];
}

- (void)restartNow {
    [self busy:@"Đang khởi động lại…"];
    [[GWA shared] send:@"POST" path:@"/gateway/restart" body:@{} done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không khởi động lại được"]; return; }
        [self toast:@"Cổng đang khởi động lại…"];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(4.0 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{ [self loadData]; });
    }];
}

@end
