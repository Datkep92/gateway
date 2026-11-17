#import "gw.h"

#pragma mark - hàng mô tả một key

GWRow *gwKeyRow(NSDictionary *k) {
    NSString *label = k[@"label"];
    if (![label isKindOfClass:[NSString class]] || label.length == 0) label = @"(không có nhãn)";
    BOOL enabled = [k[@"enabled"] boolValue];
    NSInteger fails = [k[@"failure_count"] integerValue];
    double now = [[NSDate date] timeIntervalSince1970];
    double cool = [k[@"cooldown_until"] doubleValue];

    NSMutableString *sub = [NSMutableString string];
    [sub appendFormat:@"%@ · %@ · dùng %@", gwProviderLabel(k[@"provider"] ?: @""),
                      k[@"key_prefix"] ?: @"…", gwAgo([k[@"last_used_at"] doubleValue])];
    if (!enabled) [sub appendString:@" · đang tắt"];
    else if (cool > now) [sub appendFormat:@" · nghỉ còn %@", gwDur(cool - now)];
    else if (fails > 0) [sub appendFormat:@" · %ld lần lỗi", (long)fails];

    GWRow *r = [GWRow title:label sub:sub];
    r.dotKind = (!enabled ? 4 : ((cool > now || fails > 0) ? 3 : 1));
    r.extra = k;
    return r;
}

void gwKeyTap(GWTableVC *vc, NSDictionary *k, void (^after)(void)) {
    NSString *kid = k[@"id"];
    if (kid.length == 0) return;
    BOOL enabled = [k[@"enabled"] boolValue];
    NSString *label = k[@"label"];
    if (![label isKindOfClass:[NSString class]]) label = @"";

    NSArray<NSString *> *opts = @[@"Kiểm tra key", enabled ? @"Tắt key" : @"Bật key", @"Đổi nhãn", @"Xoá key"];
    [vc ask:@"Key" options:opts run:^(NSInteger i) {
        if (i == 0) {
            [vc busy:@"Đang thử key…"];
            [[GWA shared] send:@"POST" path:@"/gateway/keys/test" body:@{@"id": kid}
                          done:^(id j, NSError *e) {
                [vc done];
                if (e != nil) { [vc report:e title:@"Không thử được"]; return; }
                NSString *msg = j[@"message"] ?: @"";
                BOOL ok = [j[@"ok"] boolValue];
                [vc toast:(ok ? @"Key dùng được" : [NSString stringWithFormat:@"Key lỗi: %@", msg])];
                if (after) after();
            }];
        } else if (i == 1) {
            [vc busy:@"Đang lưu…"];
            [[GWA shared] send:@"PATCH" path:[NSString stringWithFormat:@"/gateway/keys/%@", kid]
                          body:@{@"enabled": @(!enabled)} done:^(id j, NSError *e) {
                [vc done];
                if (e != nil) { [vc report:e title:@"Không lưu được"]; return; }
                if (after) after();
            }];
        } else if (i == 2) {
            [vc prompt:@"Nhãn của key" value:label run:^(NSString *text) {
                [vc busy:@"Đang lưu…"];
                [[GWA shared] send:@"PATCH" path:[NSString stringWithFormat:@"/gateway/keys/%@", kid]
                              body:@{@"label": text} done:^(id j, NSError *e) {
                    [vc done];
                    if (e != nil) { [vc report:e title:@"Không lưu được"]; return; }
                    if (after) after();
                }];
            }];
        } else if (i == 3) {
            UIAlertController *ac = [UIAlertController alertControllerWithTitle:@"Xoá key này?"
                                                                      message:@"Không lấy lại được."
                                                               preferredStyle:UIAlertControllerStyleAlert];
            [ac addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
            [ac addAction:[UIAlertAction actionWithTitle:@"Xoá" style:UIAlertActionStyleDestructive
                                                handler:^(UIAlertAction *a) {
                [vc busy:@"Đang xoá…"];
                [[GWA shared] send:@"DELETE" path:[NSString stringWithFormat:@"/gateway/keys/%@", kid]
                              body:nil done:^(id j, NSError *e) {
                    [vc done];
                    if (e != nil) { [vc report:e title:@"Không xoá được"]; return; }
                    if (after) after();
                }];
            }]];
            [vc presentViewController:ac animated:YES completion:nil];
        }
    }];
}

#pragma mark - danh sách key

@implementation KeysVC

- (void)viewDidLoad {
    self.title = @"Key API";
    [super viewDidLoad];
    self.navigationItem.rightBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemAdd
                                                      target:self action:@selector(addKey)];
}

- (void)addKey { [self push:[[KeyAddVC alloc] init]]; }

- (void)loadData {
    [self.sections removeAllObjects];
    GWSection *s = [self addSection:nil footer:nil];
    GWRow *w = [self addRow:s title:@"Đang đọc…" sub:nil];
    w.dotKind = 4;
    [self.tableView reloadData];

    [[GWA shared] get:@"/gateway/keys" query:nil done:^(id j, NSError *e) {
        [self.sections removeAllObjects];
        if (e != nil) {
            GWSection *bad = [self addSection:nil footer:nil];
            [self addRow:bad title:@"Không đọc được" sub:e.localizedDescription].dotKind = 2;
            [self addAddRow];
            [self.tableView reloadData];
            return;
        }
        NSArray *keys = [j[@"keys"] isKindOfClass:[NSArray class]] ? j[@"keys"] : @[];
        NSMutableDictionary<NSString *, NSMutableArray *> *groups = [NSMutableDictionary dictionary];
        for (NSDictionary *k in keys) {
            NSString *prov = k[@"provider"] ?: @"khác";
            if (groups[prov] == nil) groups[prov] = [NSMutableArray array];
            [groups[prov] addObject:k];
        }
        NSArray *names = [[groups allKeys] sortedArrayUsingSelector:@selector(compare:)];
        for (NSString *prov in names) {
            NSArray *list = groups[prov];
            GWSection *sec = [self addSection:[NSString stringWithFormat:@"%@ (%lu)",
                                               gwProviderLabel(prov), (unsigned long)list.count] footer:nil];
            for (NSDictionary *k in list) {
                GWRow *r = gwKeyRow(k);
                [sec.rows addObject:r];
                gwKeyTap(self, k, ^{ [self loadData]; });
            }
        }
        if (keys.count == 0) {
            GWSection *empty = [self addSection:nil footer:nil];
            [self addRow:empty title:@"Chưa có key nào" sub:@"Thêm key của nhà cung cấp để cổng gọi được API."].dotKind = 4;
        }
        [self addAddRow];
        [self.tableView reloadData];
    }];
}

- (void)addAddRow {
    GWSection *s = [self addSection:nil
                             footer:@"Chạm vào một key để kiểm tra, bật/tắt, đổi nhãn hoặc xoá."];
    GWRow *r = [self addRow:s title:@"＋ Thêm key" sub:nil];
    r.tap = ^(GWRow *row) { [self addKey]; };
}

@end

#pragma mark - thêm key

@interface KeyAddVC ()
@property (nonatomic, copy) NSString *label;
@property (nonatomic, copy) NSString *keyValue;
@end

@implementation KeyAddVC

- (void)viewDidLoad {
    self.title = @"Thêm key";
    [super viewDidLoad];
    self.label = @"";
    self.keyValue = @"";
    if (self.provider.length == 0) self.provider = @"";
}

- (void)loadData {
    [self.sections removeAllObjects];
    GWSection *s = [self addSection:nil
                             footer:@"Địa chỉ của nhà cung cấp cổng đã biết sẵn; chỉ nhà cung cấp tự nhập mới cần sửa địa chỉ."];

    GWRow *p = [self addRow:s title:@"Nhà cung cấp"
                         sub:self.provider.length ? gwProviderLabel(self.provider) : @"Chạm để chọn"];
    p.chevron = YES;
    p.tap = ^(GWRow *r) { [self pickProvider]; };

    GWRow *l = [self addRow:s title:@"Nhãn" sub:(self.label.length ? self.label : @"Không bắt buộc")];
    l.tap = ^(GWRow *r) {
        [self prompt:@"Nhãn cho key này" value:self.label run:^(NSString *text) {
            self.label = text;
            [self reload];
        }];
    };

    GWRow *k = [self addRow:s title:@"Key của nhà cung cấp"
                         sub:(self.keyValue.length ? [NSString stringWithFormat:@"đã nhập (%lu ký tự)",
                                                      (unsigned long)self.keyValue.length] : @"Chạm để dán key")];
    k.tap = ^(GWRow *r) {
        [self prompt:@"Dán key của nhà cung cấp" value:@"" run:^(NSString *text) {
            self.keyValue = text;
            [self reload];
        }];
    };

    GWSection *act = [self addSection:nil footer:nil];
    GWRow *go = [self addRow:act title:@"Thêm key" sub:@"Cổng sẽ tự dò model ngay sau khi thêm"];
    go.tap = ^(GWRow *r) { [self submit]; };
    [self.tableView reloadData];
}

- (void)pickProvider {
    NSArray<NSDictionary *> *all = gwKnownProviders();
    NSMutableArray<NSString *> *labels = [NSMutableArray array];
    for (NSDictionary *p in all) [labels addObject:p[@"label"]];
    [self ask:@"Nhà cung cấp" options:labels run:^(NSInteger i) {
        self.provider = all[(NSUInteger)i][@"name"];
        [self reload];
    }];
}

- (void)submit {
    if (self.provider.length == 0) { [self fail:@"Chọn nhà cung cấp trước đã."]; return; }
    if (self.keyValue.length < 8) { [self fail:@"Key chưa nhập (hoặc quá ngắn)."]; return; }
    NSString *provider = self.provider, *label = self.label, *key = self.keyValue;
    [self busy:@"Đang thêm key và dò model…"];
    NSDictionary *body = @{@"provider": provider, @"label": label ?: @"", @"key": key, @"priority": @0};
    [[GWA shared] send:@"POST" path:@"/gateway/keys" body:body done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không thêm được key"]; return; }
        id models = j[@"models"];
        NSString *msg = [models isKindOfClass:[NSNumber class]]
            ? [NSString stringWithFormat:@"Đã thêm key · dò được %@ model", models]
            : @"Đã thêm key";
        [self toast:msg];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.3 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            [self.navigationController popViewControllerAnimated:YES];
        });
    }];
}

@end
