#import "gw.h"

@interface SettingsVC ()
@property (nonatomic, strong) NSDictionary *settings;
@property (nonatomic, strong) NSDictionary *autoStatus;
@property (nonatomic, strong) NSArray *clientKeys;
@property (nonatomic, strong) NSMutableSet<NSString *> *handled;
@end

@implementation SettingsVC

- (void)viewDidLoad {
    self.title = @"Cài đặt";
    [super viewDidLoad];
}

- (void)loadData {
    self.handled = [NSMutableSet set];
    [self.sections removeAllObjects];
    GWSection *s = [self addSection:nil footer:nil];
    GWRow *w = [self addRow:s title:@"Đang đọc…" sub:nil];
    w.dotKind = 4;
    [self.tableView reloadData];

    GWA *api = [GWA shared];
    [api get:@"/gateway/settings" query:nil done:^(id j, NSError *e) {
        if (e != nil) {
            [self.sections removeAllObjects];
            GWSection *bad = [self addSection:nil footer:nil];
            [self addRow:bad title:@"Không đọc được cài đặt" sub:e.localizedDescription].dotKind = 2;
            [self.tableView reloadData];
            return;
        }
        self.settings = j[@"settings"] ?: @{};
        [api get:@"/gateway/auto/status" query:nil done:^(id j2, NSError *e2) {
            self.autoStatus = (e2 == nil && [j2 isKindOfClass:[NSDictionary class]]) ? j2 : @{};
            [api get:@"/gateway/client-keys" query:nil done:^(id j3, NSError *e3) {
                self.clientKeys = (e3 == nil && [j3[@"client_keys"] isKindOfClass:[NSArray class]])
                    ? j3[@"client_keys"] : @[];
                [self build];
            }];
        }];
    }];
}

- (NSString *)v:(NSString *)key {
    id v = self.settings[key];
    if ([v isKindOfClass:[NSString class]]) return v;
    if (v == nil) return @"";
    return [v description];
}

- (BOOL)on:(NSString *)key {
    NSString *v = [[self v:key] lowercaseString];
    return [v isEqualToString:@"1"] || [v isEqualToString:@"true"] || [v isEqualToString:@"on"];
}

- (void)build {
    [self.sections removeAllObjects];
    [self buildAuto];
    [self buildServer];
    [self buildOthers];
    [self buildClientKeys];
    [self buildMaintenance];
    [self.tableView reloadData];
}

#pragma mark - tự kiểm

- (void)buildAuto {
    NSDictionary *a = self.autoStatus;
    BOOL on = [a[@"enabled"] boolValue];
    BOOL busy = [a[@"busy"] boolValue];
    NSInteger due = [a[@"pending_models"] integerValue] + [a[@"due_keys"] integerValue];
    double next = [a[@"next_at"] doubleValue];
    double now = [[NSDate date] timeIntervalSince1970];

    NSMutableString *sub = [NSMutableString string];
    if (![a[@"enabled"] isKindOfClass:[NSNumber class]]) [sub appendString:@"chưa đọc được trạng thái"];
    else if (!on) [sub appendString:@"đang tắt"];
    else if (busy) [sub appendString:@"đang kiểm"];
    else if (due > 0) [sub appendFormat:@"còn %ld việc tới hạn", (long)due];
    else if (next > now) [sub appendFormat:@"kiểm tiếp sau %@", gwDur(next - now)];
    else [sub appendString:@"đang bật"];
    double last = [a[@"last_finish"] doubleValue];
    if (last > 0) [sub appendFormat:@" · lần cuối %@", gwAgo(last)];

    GWSection *s = [self addSection:@"Tự kiểm key & model"
        footer:@"Vòng tự kiểm tự thử model và key ở nền, không cần bấm 🧪. Lỗi tạm thời không bị coi là key hỏng."];

    GWRow *st = [self addRow:s title:@"Trạng thái" sub:sub];
    st.dotKind = (!on ? 4 : (busy ? 3 : (due > 0 ? 3 : 1)));
    NSString *msg = a[@"last_message"];
    if ([msg isKindOfClass:[NSString class]] && msg.length > 0) {
        st.tap = ^(GWRow *r) {
            UIAlertController *ac = [UIAlertController alertControllerWithTitle:@"Lần kiểm gần nhất"
                                                                       message:msg
                                                                preferredStyle:UIAlertControllerStyleAlert];
            [ac addAction:[UIAlertAction actionWithTitle:@"Đóng" style:UIAlertActionStyleDefault handler:nil]];
            [self presentViewController:ac animated:YES completion:nil];
        };
    }

    GWRow *now2 = [self addRow:s title:@"Kiểm ngay" sub:@"Chạy một vòng kiểm ở luồng riêng"];
    now2.tap = ^(GWRow *r) { [self checkNow]; };

    [self switchRow:s key:@"auto_check" title:@"Bật tự kiểm" sub:@"Tắt thì cổng không tự thử key/model nữa"];
    [self textRow:s key:@"auto_check_secs" title:@"Chu kỳ kiểm (giây)"];
    [self textRow:s key:@"auto_check_batch" title:@"Số model mỗi vòng"];
    [self textRow:s key:@"model_stale_secs" title:@"Kiểm lại model tốt sau (giây)"];
    [self textRow:s key:@"model_retry_secs" title:@"Thử lại model lỗi sau (giây)"];
    [self textRow:s key:@"key_retry_secs" title:@"Thử lại key lỗi sau giây"];
    [self textRow:s key:@"key_wait_max" title:@"Số key thử lại mỗi vòng"];
}

- (void)checkNow {
    [self busy:@"Đang kiểm…"];
    [[GWA shared] send:@"POST" path:@"/gateway/auto/check" body:@{@"force": @YES}
                  done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không kiểm được"]; return; }
        [self toast:@"Đang kiểm ở nền"];
        [self loadData];
    }];
}

#pragma mark - máy chủ

- (void)buildServer {
    GWSection *s = [self addSection:@"Máy chủ"
        footer:@"Đổi cổng (port) xong phải khởi động lại cổng mới áp dụng."];
    [self textRow:s key:@"port" title:@"Cổng (port)"];
    [self textRow:s key:@"host" title:@"Địa chỉ nghe"];
    [self switchRow:s key:@"client_key_required" title:@"Bắt buộc client key" sub:@"Tắt thì ai vào máy cũng gọi được"];
    [self switchRow:s key:@"lan_enabled" title:@"Cho máy khác trong mạng LAN" sub:@"Mở cổng ra ngoài 127.0.0.1"];
    [self switchRow:s key:@"tls_enabled" title:@"Bật HTTPS (TLS)"];
    [self textRow:s key:@"tls_cert_path" title:@"Tệp chứng chỉ"];
    [self textRow:s key:@"tls_key_path" title:@"Tệp khoá"];
    [self switchRow:s key:@"cors_enabled" title:@"Cho phép CORS"];
    [self switchRow:s key:@"rate_limit_enabled" title:@"Giới hạn số lần gọi"];
    [self textRow:s key:@"rate_limit_rpm" title:@"Số lần gọi mỗi phút"];
    [self textRow:s key:@"rate_limit_burst" title:@"Số lần gọi dồn"];
    [self textRow:s key:@"log_retention_days" title:@"Giữ nhật ký (ngày)"];
    [self textRow:s key:@"max_log_count" title:@"Số dòng nhật ký giữ lại"];
    [self switchRow:s key:@"log_body_enabled" title:@"Ghi nội dung request vào log" sub:@"Bật thì log dài và lộ nội dung"];
}

#pragma mark - cài đặt khác

- (void)buildOthers {
    NSArray *keys = [[self.settings allKeys] sortedArrayUsingSelector:@selector(compare:)];
    NSMutableArray<NSString *> *rest = [NSMutableArray array];
    for (NSString *k in keys) {
        if ([k hasPrefix:@"_"]) continue;
        if ([self.handled containsObject:k]) continue;
        [rest addObject:k];
    }
    if (rest.count == 0) return;
    GWSection *s = [self addSection:@"Cài đặt khác"
                             footer:@"Các cài đặt còn lại của cổng. Chạm để sửa, gõ đúng giá trị."];
    for (NSString *k in rest) {
        GWRow *r = [self addRow:s title:k sub:nil];
        r.value = [self v:k];
        NSString *key = k;
        r.tap = ^(GWRow *row) {
            [self prompt:key value:[self v:key] run:^(NSString *text) {
                [self save:key value:text];
            }];
        };
    }
}

#pragma mark - key của chính máy này

- (void)buildClientKeys {
    GWSection *s = [self addSection:[NSString stringWithFormat:@"Key cho máy khác gọi vào (%lu)",
                                     (unsigned long)self.clientKeys.count]
        footer:@"Key này là thứ các máy khác dùng để gọi cổng (header Authorization: Bearer). App này dùng key riêng trong tệp data/app.key."];
    for (NSDictionary *k in self.clientKeys) {
        NSString *name = k[@"name"];
        if (![name isKindOfClass:[NSString class]] || name.length == 0) name = @"(không tên)";
        NSString *kid = k[@"id"];
        GWRow *r = [self addRow:s title:name
                            sub:[NSString stringWithFormat:@"tạo %@ · dùng %@",
                                 gwAgo([k[@"created_at"] doubleValue]), gwAgo([k[@"last_used_at"] doubleValue])]];
        r.dotKind = [k[@"enabled"] boolValue] ? 1 : 4;
        if (kid.length) {
            r.tap = ^(GWRow *row) { [self clientKeySheet:kid name:name]; };
        }
    }
    GWRow *add = [self addRow:s title:@"＋ Tạo key mới" sub:@"Key hiện ra một lần, chép lại rồi đưa cho máy kia"];
    add.tap = ^(GWRow *row) { [self newClientKey]; };
}

- (void)clientKeySheet:(NSString *)kid name:(NSString *)name {
    [self ask:name options:@[@"Xoá key này"] run:^(NSInteger i) {
        if (i != 0) return;
        UIAlertController *ac = [UIAlertController
            alertControllerWithTitle:[NSString stringWithFormat:@"Xoá key “%@”?", name]
                             message:@"Máy nào đang dùng key này sẽ bị từ chối."
                      preferredStyle:UIAlertControllerStyleAlert];
        [ac addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
        [ac addAction:[UIAlertAction actionWithTitle:@"Xoá" style:UIAlertActionStyleDestructive
                                            handler:^(UIAlertAction *a) {
            [self busy:@"Đang xoá…"];
            [[GWA shared] send:@"DELETE" path:[NSString stringWithFormat:@"/gateway/client-keys/%@", kid]
                          body:nil done:^(id j, NSError *e) {
                [self done];
                if (e != nil) { [self report:e title:@"Không xoá được"]; return; }
                [self loadData];
            }];
        }]];
        [self presentViewController:ac animated:YES completion:nil];
    }];
}

- (void)newClientKey {
    [self prompt:@"Đặt tên cho key mới" value:@"" run:^(NSString *name) {
        [self busy:@"Đang tạo…"];
        [[GWA shared] send:@"POST" path:@"/gateway/client-keys" body:@{@"name": name}
                      done:^(id j, NSError *e) {
            [self done];
            if (e != nil) { [self report:e title:@"Không tạo được"]; return; }
            NSString *key = j[@"key"];
            if (![key isKindOfClass:[NSString class]] || key.length == 0) {
                [self fail:@"Máy chủ không trả về key."];
                return;
            }
            UIAlertController *ac = [UIAlertController
                alertControllerWithTitle:@"Key mới" message:key
                         preferredStyle:UIAlertControllerStyleAlert];
            [ac addAction:[UIAlertAction actionWithTitle:@"Chép" style:UIAlertActionStyleDefault
                                                handler:^(UIAlertAction *a) {
                [UIPasteboard generalPasteboard].string = key;
                [self toast:@"Đã chép key"];
            }]];
            [ac addAction:[UIAlertAction actionWithTitle:@"Đóng" style:UIAlertActionStyleDefault handler:nil]];
            [self presentViewController:ac animated:YES completion:nil];
            [self loadData];
        }];
    }];
}

#pragma mark - việc khác

- (void)buildMaintenance {
    GWSection *s = [self addSection:nil footer:nil];
    GWRow *r = [self addRow:s title:@"Khởi động lại cổng"
                         sub:@"Áp dụng cài đặt mới (cổng, TLS, giới hạn…)"];
    r.tap = ^(GWRow *row) { [self restartGateway]; };
}

- (void)restartGateway {
    [self busy:@"Đang khởi động lại…"];
    [[GWA shared] send:@"POST" path:@"/gateway/restart" body:@{} done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không khởi động lại được"]; return; }
        [self toast:@"Cổng đang khởi động lại…"];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(4.0 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{ [self loadData]; });
    }];
}

#pragma mark - hàng dùng chung

- (GWRow *)switchRow:(GWSection *)s key:(NSString *)key title:(NSString *)title {
    return [self switchRow:s key:key title:title sub:nil];
}

- (GWRow *)switchRow:(GWSection *)s key:(NSString *)key title:(NSString *)title sub:(NSString *)sub {
    GWRow *r = [self addRow:s title:title sub:sub];
    r.hasSwitch = YES;
    r.switchOn = [self on:key];
    NSString *k = key;
    r.toggle = ^(BOOL on2) { [self save:k value:(on2 ? @"1" : @"0")]; };
    [self.handled addObject:key];
    return r;
}

- (GWRow *)textRow:(GWSection *)s key:(NSString *)key title:(NSString *)title {
    GWRow *r = [self addRow:s title:title sub:nil];
    r.value = [self v:key];
    NSString *k = key;
    r.tap = ^(GWRow *row) {
        [self prompt:title value:[self v:k] run:^(NSString *text) { [self save:k value:text]; }];
    };
    [self.handled addObject:key];
    return r;
}

- (void)save:(NSString *)key value:(NSString *)value {
    // Đúng giá trị đang lưu thì không gửi lại: vẽ lại bảng không được sinh request.
    if ([self.handled containsObject:key] && [[self v:key] isEqualToString:(value ?: @"")]) return;
    static int dbgSave = 0;
    if (dbgSave < 10) { dbgSave++; NSLog(@"DBG save %@=%@ st=%@", key, value, gwStack()); }
    [self busy:@"Đang lưu…"];
    [[GWA shared] send:@"POST" path:@"/gateway/settings" body:@{key: value ?: @""}
                  done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không lưu được"]; return; }
        NSMutableDictionary *d = [NSMutableDictionary dictionaryWithDictionary:self.settings];
        d[key] = value ?: @"";
        self.settings = d;
        if ([key isEqualToString:@"port"] || [key isEqualToString:@"host"]
            || [key isEqualToString:@"tls_enabled"] || [key isEqualToString:@"lan_enabled"]
            || [key isEqualToString:@"rate_limit_enabled"]) {
            [self toast:@"Đã lưu · khởi động lại cổng để áp dụng"];
        } else {
            [self toast:@"Đã lưu"];
        }
        [self.tableView reloadData];
    }];
}

@end
