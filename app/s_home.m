#import "gw.h"

#pragma mark - màn hình chính

@implementation HomeVC

- (void)viewDidLoad {
    self.title = @"Cổng";
    [super viewDidLoad];
    self.navigationItem.rightBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemRefresh
                                                      target:self action:@selector(loadData)];
}

- (void)loadData {
    [self.sections removeAllObjects];
    GWSection *s = [self addSection:nil footer:nil];
    GWRow *wait = [self addRow:s title:@"Đang kiểm tra…" sub:nil];
    wait.dotKind = 4;
    [self.tableView reloadData];

    gwBG(^{
        NSString *word = gwStateNow();
        GWA *api = [GWA shared];
        if (![word isEqualToString:@"dang_chay"]) [api refresh];
        [api get:@"/gateway/status" query:nil done:^(id j, NSError *e) {
            [self buildTop:word status:(e ? nil : j)];
            [self loadCounts];
        }];
    });
}

- (void)buildTop:(NSString *)word status:(NSDictionary *)j {
    [self.sections removeAllObjects];
    BOOL live = (j != nil);

    GWSection *s = [self addSection:nil
                             footer:@"Cổng chỉ chạy khi cần: app tự mở khi bạn mở app, và tự tắt khi Apelin hết việc."];
    GWRow *st;
    if (live) {
        NSString *port = [j[@"port"] description] ?: @"15199";
        st = [self addRow:s title:@"Cổng đang chạy"
                      sub:[NSString stringWithFormat:@"127.0.0.1:%@ · đã chạy %@", port, gwDur([j[@"uptime"] doubleValue])]];
        st.dotKind = 1;
    } else if ([word isEqualToString:@"dang_treo"]) {
        st = [self addRow:s title:@"Cổng đang dựng" sub:@"Có tiến trình nhưng chưa trả lời — chờ vài giây rồi tải lại."];
        st.dotKind = 3;
    } else {
        st = [self addRow:s title:@"Cổng chưa chạy" sub:@"Bấm “Mở cổng” để chạy lại."];
        st.dotKind = 2;
    }

    GWRow *power;
    if (live) {
        power = [self addRow:s title:@"Tắt cổng" sub:@"Dừng hẳn tiến trình cổng"];
        power.tap = ^(GWRow *r) { [self stopGateway]; };
    } else {
        power = [self addRow:s title:@"Mở cổng" sub:@"Chạy cổng ở chế độ chạy tay"];
        power.tap = ^(GWRow *r) { [self startGateway]; };
    }

    GWRow *reload = [self addRow:s title:@"Tải lại" sub:nil];
    reload.tap = ^(GWRow *r) { [self loadData]; };

    if (live) {
        GWRow *web = [self addRow:s title:@"Mở trang quản lý trong Safari" sub:@"Bản web đầy đủ (dành cho máy khác)"];
        web.tap = ^(GWRow *r) { [self openWeb]; };
    }

    [self addLinks];
    [self.tableView reloadData];
    if (!live) [self autoStart];
}

- (void)addLinks {
    GWSection *mg = [self addSection:@"Quản lý" footer:nil];
    GWRow *prov = [self addRow:mg title:@"Nhà cung cấp" sub:@"đang đọc…"];
    prov.chevron = YES;
    prov.extra = @"providers";
    prov.tap = ^(GWRow *r) { [self push:[[ProvidersVC alloc] init]]; };

    GWRow *keys = [self addRow:mg title:@"Key API" sub:@"đang đọc…"];
    keys.chevron = YES;
    keys.extra = @"keys";
    keys.tap = ^(GWRow *r) { [self push:[[KeysVC alloc] init]]; };

    GWRow *models = [self addRow:mg title:@"Model" sub:@"đang đọc…"];
    models.chevron = YES;
    models.extra = @"models";
    models.tap = ^(GWRow *r) { [self push:[[ModelsVC alloc] init]]; };

    GWRow *auto1 = [self addRow:mg title:@"Tự kiểm key & model" sub:@"đang đọc…"];
    auto1.chevron = YES;
    auto1.extra = @"auto";
    auto1.tap = ^(GWRow *r) { [self push:[[SettingsVC alloc] init]]; };

    GWRow *logs = [self addRow:mg title:@"Nhật ký gọi API" sub:@"Các lượt gọi gần đây"];
    logs.chevron = YES;
    logs.tap = ^(GWRow *r) { [self push:[[LogsVC alloc] init]]; };

    GWSection *more = [self addSection:nil footer:nil];
    GWRow *set = [self addRow:more title:@"Cài đặt" sub:@"Cổng, key ứng dụng, tự kiểm"];
    set.chevron = YES;
    set.tap = ^(GWRow *r) { [self push:[[SettingsVC alloc] init]]; };

    GWRow *backup = [self addRow:more title:@"Sao lưu & khôi phục" sub:@"Xuất hoặc nhập toàn bộ cấu hình"];
    backup.chevron = YES;
    backup.tap = ^(GWRow *r) { [self push:[[BackupVC alloc] init]]; };
}

- (GWRow *)linkRow:(NSString *)key {
    for (GWSection *s in self.sections) {
        for (GWRow *r in s.rows) {
            if ([r.extra isKindOfClass:[NSString class]] && [r.extra isEqualToString:key]) return r;
        }
    }
    return nil;
}

- (void)loadCounts {
    GWA *api = [GWA shared];

    [api get:@"/gateway/providers" query:nil done:^(id j, NSError *e) {
        GWRow *r = [self linkRow:@"providers"];
        NSArray *ps = j[@"providers"];
        if (r == nil) return;
        if (e != nil || ![ps isKindOfClass:[NSArray class]]) {
            r.sub = @"chưa đọc được (cổng chưa chạy?)";
        } else {
            NSInteger keys = 0, models = 0;
            for (NSDictionary *p in ps) {
                keys += [p[@"key_count"] integerValue];
                models += [p[@"model_count"] integerValue];
            }
            r.sub = [NSString stringWithFormat:@"%lu nhà cung cấp · %ld key · %ld model",
                     (unsigned long)ps.count, (long)keys, (long)models];
        }
        [self.tableView reloadData];
    }];

    [api get:@"/gateway/keys" query:nil done:^(id j, NSError *e) {
        GWRow *r = [self linkRow:@"keys"];
        NSArray *ks = j[@"keys"];
        if (r == nil) return;
        if (e != nil || ![ks isKindOfClass:[NSArray class]]) {
            r.sub = @"chưa đọc được";
        } else {
            NSInteger off = 0;
            double now = [[NSDate date] timeIntervalSince1970];
            for (NSDictionary *k in ks) {
                if ([k[@"enabled"] boolValue] == NO) { off++; continue; }
                if ([k[@"cooldown_until"] doubleValue] > now) off++;
            }
            if (ks.count == 0) r.sub = @"chưa có key nào";
            else if (off) r.sub = [NSString stringWithFormat:@"%lu key · %ld đang nghỉ", (unsigned long)ks.count, (long)off];
            else r.sub = [NSString stringWithFormat:@"%lu key, tất cả đang bật", (unsigned long)ks.count];
        }
        [self.tableView reloadData];
    }];

    [api get:@"/gateway/models" query:nil done:^(id j, NSError *e) {
        GWRow *r = [self linkRow:@"models"];
        NSArray *ms = j[@"models"];
        if (r == nil) return;
        if (e != nil || ![ms isKindOfClass:[NSArray class]]) {
            r.sub = @"chưa đọc được";
        } else {
            NSInteger on = 0, bad = 0, ok = 0;
            for (NSDictionary *m in ms) {
                if ([m[@"enabled"] integerValue] == 1) on++;
                NSString *h = m[@"health"];
                if ([h isEqual:@"fail"]) bad++;
                else if ([h isEqual:@"ok"]) ok++;
            }
            r.sub = [NSString stringWithFormat:@"%lu model · %ld bật · %ld tốt · %ld lỗi",
                     (unsigned long)ms.count, (long)on, (long)ok, (long)bad];
        }
        [self.tableView reloadData];
    }];

    [api get:@"/gateway/auto/status" query:nil done:^(id j, NSError *e) {
        GWRow *r = [self linkRow:@"auto"];
        if (r == nil) return;
        if (e != nil || ![j isKindOfClass:[NSDictionary class]]) {
            r.sub = @"chưa đọc được";
        } else {
            BOOL on = [j[@"enabled"] boolValue];
            NSString *last = gwAgo([j[@"last_finish"] doubleValue]);
            NSInteger due = [j[@"pending_models"] integerValue] + [j[@"due_keys"] integerValue];
            NSString *s = on ? @"đang bật" : @"đang tắt";
            if ([j[@"busy"] boolValue]) s = [s stringByAppendingString:@" · đang kiểm"];
            s = [s stringByAppendingFormat:@" · lần cuối %@", last];
            if (due) s = [s stringByAppendingFormat:@" · %ld việc tồn", (long)due];
            r.sub = s;
        }
        [self.tableView reloadData];
    }];
}

#pragma mark hành động

- (void)autoStart {
    static BOOL tried = NO;
    if (tried) return;
    tried = YES;
    [self busy:@"Đang mở cổng…"];
    gwBG(^{
        NSString *out = gwCtl(@[@"on"]);
        gwLog(@"app mở cổng: %@", gwTrim(out).length ? gwTrim(out) : @"(không có kết quả)");
        gwMain(^{
            [self done];
            [self loadData];
        });
    });
}

- (void)startGateway {
    [self busy:@"Đang mở cổng…"];
    gwBG(^{
        gwCtl(@[@"on"]);
        gwMain(^{ [self done]; [self loadData]; });
    });
}

- (void)stopGateway {
    UIAlertController *ac = [UIAlertController alertControllerWithTitle:@"Tắt cổng?"
                                                              message:@"Mọi tiến trình cổng sẽ dừng; mở lại bằng nút trong app."
                                                       preferredStyle:UIAlertControllerStyleAlert];
    [ac addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    [ac addAction:[UIAlertAction actionWithTitle:@"Tắt" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *a) {
        [self busy:@"Đang tắt cổng…"];
        gwBG(^{
            gwCtl(@[@"off"]);
            gwMain(^{ [self done]; [self loadData]; });
        });
    }]];
    [self presentViewController:ac animated:YES completion:nil];
}

- (void)openWeb {
    NSString *key = [GWA shared].key;
    NSString *q = [key stringByAddingPercentEncodingWithAllowedCharacters:[NSCharacterSet URLQueryAllowedCharacterSet]];
    NSString *url = [NSString stringWithFormat:@"%@/?key=%@", [GWA shared].base ?: kFallbackURL, q ?: @""];
    gwLog(@"mở trang web: %@", url.length ? @"có key" : @"");
    gwOpenURL(url);
}

@end

#pragma mark - nhật ký

@implementation LogsVC

- (void)viewDidLoad {
    self.title = @"Nhật ký";
    [super viewDidLoad];
}

- (void)loadData {
    [self.sections removeAllObjects];
    GWSection *s = [self addSection:nil footer:nil];
    GWRow *wait = [self addRow:s title:@"Đang đọc…" sub:nil];
    wait.dotKind = 4;
    [self.tableView reloadData];

    [[GWA shared] get:@"/gateway/logs" query:@{@"limit": @150} done:^(id j, NSError *e) {
        [self.sections removeAllObjects];
        GWSection *sec = [self addSection:nil footer:nil];
        if (e != nil) {
            [self addRow:sec title:@"Không đọc được" sub:e.localizedDescription].dotKind = 2;
        } else if ([j[@"logs"] isKindOfClass:[NSArray class]] && [j[@"logs"] count] == 0) {
            [self addRow:sec title:@"Chưa có lượt gọi nào" sub:nil].dotKind = 4;
        } else {
            NSDateFormatter *df = [[NSDateFormatter alloc] init];
            df.dateFormat = @"HH:mm:ss";
            for (NSDictionary *row in j[@"logs"]) {
                NSString *model = row[@"model"] ?: row[@"endpoint"] ?: @"?";
                NSString *prov = row[@"provider"] ?: @"-";
                NSInteger code = [row[@"status_code"] integerValue];
                NSInteger ms = [row[@"latency_ms"] integerValue];
                GWRow *r = [self addRow:sec title:model
                                    sub:[NSString stringWithFormat:@"%@ · %ld · %ld ms", prov, (long)code, (long)ms]];
                r.value = [df stringFromDate:[NSDate dateWithTimeIntervalSince1970:[row[@"timestamp"] doubleValue]]];
                r.dotKind = (code >= 400) ? 2 : 1;
            }
        }
        [self.tableView reloadData];
    }];
}

@end
